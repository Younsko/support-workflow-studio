"""Short-lived workspace authorization tokens for a single enrolled runner."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from dataclasses import dataclass
from functools import lru_cache
from time import time
from typing import Any
from urllib.parse import urlsplit

from .auth import Principal

SESSION_TTL_SECONDS = 180
SESSION_LEEWAY_SECONDS = 10
SESSION_ROLES = frozenset(
    {"Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"}
)
_KID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RESOURCE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_PRIVATE_JWK_FIELDS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})


class SessionTokenConfigurationError(RuntimeError):
    """The control-plane session-signing keys are missing or malformed."""


class SessionTokenInvalid(ValueError):
    """A runner session token is invalid or outside its configured scope."""


@dataclass(frozen=True, slots=True)
class SessionSigningConfig:
    issuer: str
    key_id: str
    private_key: Any
    public_jwks: tuple[dict[str, str], ...]

    @classmethod
    def from_env(cls) -> SessionSigningConfig:
        issuer = os.getenv("CONTROL_PLANE_SESSION_ISSUER", "").strip()
        key_id = os.getenv("CONTROL_PLANE_SESSION_KEY_ID", "").strip()
        private_pem = os.getenv("CONTROL_PLANE_SESSION_SIGNING_PRIVATE_KEY_PEM", "")
        previous_raw = os.getenv("CONTROL_PLANE_SESSION_PREVIOUS_PUBLIC_KEYS_JSON", "[]")
        external_oidc_issuer = os.getenv("CONTROL_PLANE_OIDC_ISSUER", "").strip().rstrip("/")
        parsed_issuer = urlsplit(issuer)
        if (
            parsed_issuer.scheme != "https"
            or not parsed_issuer.hostname
            or parsed_issuer.username
            or parsed_issuer.password
            or parsed_issuer.query
            or parsed_issuer.fragment
        ):
            raise SessionTokenConfigurationError(
                "The control-plane session issuer must be a clean HTTPS URL."
            )
        if not _KID_PATTERN.fullmatch(key_id):
            raise SessionTokenConfigurationError(
                "A valid control-plane session signing key ID is required."
            )
        if external_oidc_issuer and issuer.rstrip("/") == external_oidc_issuer:
            raise SessionTokenConfigurationError(
                "The runner session issuer must be separate from the external OIDC issuer."
            )
        if not private_pem or len(private_pem) > 32_768:
            raise SessionTokenConfigurationError(
                "The control-plane session signing private key is not configured."
            )
        if len(previous_raw) > 32_768:
            raise SessionTokenConfigurationError(
                "The previous public session key set is too large."
            )
        try:
            from cryptography.exceptions import UnsupportedAlgorithm
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key,
            )
        except ImportError as exc:
            raise SessionTokenConfigurationError(
                "The control-plane session signing key requires its cryptographic backend."
            ) from exc
        try:
            private_key = load_pem_private_key(private_pem.encode("utf-8"), password=None)
        except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
            raise SessionTokenConfigurationError(
                "The control-plane session signing private key could not be loaded."
            ) from exc
        if not isinstance(private_key, RSAPrivateKey) or private_key.key_size < 2048:
            raise SessionTokenConfigurationError(
                "The control-plane session signing key must be RSA with at least 2048 bits."
            )
        numbers = private_key.public_key().public_numbers()
        if numbers.e < 3 or numbers.e % 2 == 0:
            raise SessionTokenConfigurationError(
                "The control-plane session signing key has an invalid RSA exponent."
            )
        active_jwk = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": key_id,
            "n": _base64url_integer(numbers.n),
            "e": _base64url_integer(numbers.e),
        }
        try:
            previous_keys = json.loads(previous_raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SessionTokenConfigurationError(
                "Previous public signing keys must be a JSON array."
            ) from exc
        if not isinstance(previous_keys, list) or len(previous_keys) > 8:
            raise SessionTokenConfigurationError(
                "At most eight previous public signing keys may be configured."
            )
        try:
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        except ImportError as exc:
            raise SessionTokenConfigurationError(
                "The control-plane session signing key requires its cryptographic backend."
            ) from exc
        public_jwks: list[dict[str, str]] = [active_jwk]
        known_kids = {key_id}
        for item in previous_keys:
            if (
                not isinstance(item, dict)
                or _PRIVATE_JWK_FIELDS.intersection(item)
                or set(item) - {"kty", "use", "alg", "kid", "n", "e"}
            ):
                raise SessionTokenConfigurationError(
                    "Previous session keys must contain public JWK values only."
                )
            kid = item.get("kid")
            if (
                not isinstance(kid, str)
                or not _KID_PATTERN.fullmatch(kid)
                or kid in known_kids
                or item.get("kty") != "RSA"
                or not isinstance(item.get("n"), str)
                or not isinstance(item.get("e"), str)
                or (item.get("alg") not in {None, "RS256"})
                or (item.get("use") not in {None, "sig"})
            ):
                raise SessionTokenConfigurationError(
                    "A previous session public JWK is invalid or has a duplicate key ID."
                )
            try:
                modulus = _decode_base64url_integer(item["n"])
                exponent = _decode_base64url_integer(item["e"])
                public_key = RSAPublicNumbers(exponent, modulus).public_key()
            except (TypeError, ValueError) as exc:
                raise SessionTokenConfigurationError(
                    "A previous session public JWK contains invalid RSA parameters."
                ) from exc
            if public_key.key_size < 2048 or exponent < 3 or exponent % 2 == 0:
                raise SessionTokenConfigurationError(
                    "A previous session public JWK must use RSA with at least 2048 bits."
                )
            known_kids.add(kid)
            public_jwks.append(
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": kid,
                    "n": item["n"],
                    "e": item["e"],
                }
            )
        return cls(
            issuer=issuer,
            key_id=key_id,
            private_key=private_key,
            public_jwks=tuple(public_jwks),
        )


def _base64url_integer(value: int) -> str:
    encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _decode_base64url_integer(value: str) -> int:
    if not value or len(value) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid base64url integer.")
    normalized = value + "=" * (-len(value) % 4)
    decoded = base64.b64decode(normalized, altchars=b"-_", validate=True)
    if not decoded:
        raise ValueError("Invalid base64url integer.")
    return int.from_bytes(decoded, "big")


def public_jwks(config: SessionSigningConfig | None = None) -> dict[str, list[dict[str, str]]]:
    return {"keys": list((config or SessionSigningConfig.from_env()).public_jwks)}


def issue_workspace_session(
    principal: Principal,
    *,
    workspace_id: str,
    runner_id: str,
    role: str,
    config: SessionSigningConfig | None = None,
    now: int | None = None,
) -> tuple[str, int]:
    if (
        role not in SESSION_ROLES
        or not _RESOURCE_ID_PATTERN.fullmatch(workspace_id)
        or not _RESOURCE_ID_PATTERN.fullmatch(runner_id)
    ):
        raise SessionTokenInvalid("Workspace role is invalid.")
    if not principal.subject or principal.expires_at is None:
        raise SessionTokenInvalid("A verified identity token expiry is required.")
    signing = config or SessionSigningConfig.from_env()
    timestamp = int(time()) if now is None else now
    expires_at = min(timestamp + SESSION_TTL_SECONDS, principal.expires_at)
    if expires_at <= timestamp:
        raise SessionTokenInvalid("The identity token is expired.")
    try:
        import jwt
    except ImportError as exc:
        raise SessionTokenConfigurationError(
            "Runner sessions require the optional 'control-plane' package extra."
        ) from exc
    claims = {
        "iss": signing.issuer,
        "sub": principal.subject,
        "aud": runner_id,
        "workspace_id": workspace_id,
        "runner_id": runner_id,
        "role": role,
        "iat": timestamp,
        "nbf": timestamp,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(24),
    }
    token = jwt.encode(
        claims,
        signing.private_key,
        algorithm="RS256",
        headers={"kid": signing.key_id, "typ": "JWT"},
    )
    return token, expires_at


@lru_cache(maxsize=8)
def _jwks_client(uri: str) -> Any:
    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError(
            "Runner session validation requires the optional 'control-plane' extra."
        ) from exc
    return jwt.PyJWKClient(uri, cache_jwk_set=True, lifespan=60, timeout=5)


def verify_workspace_session(
    token: str,
    *,
    issuer: str,
    jwks_uri: str,
    audience: str,
    workspace_id: str,
    runner_id: str,
    jwks_client: Any | None = None,
) -> dict[str, Any]:
    if not token or len(token) > 16_384:
        raise SessionTokenInvalid("Runner session token is invalid.")
    for value, label in ((issuer, "issuer"), (jwks_uri, "JWKS URI")):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(f"The runner session {label} must be a clean HTTPS URL.")
    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError(
            "Runner session validation requires the optional 'control-plane' extra."
        ) from exc
    client = jwks_client or _jwks_client(jwks_uri)
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            leeway=SESSION_LEEWAY_SECONDS,
            options={
                "require": [
                    "iss",
                    "sub",
                    "aud",
                    "workspace_id",
                    "runner_id",
                    "role",
                    "iat",
                    "nbf",
                    "exp",
                    "jti",
                ]
            },
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise RuntimeError("Runner session signing keys are unavailable.") from exc
    except jwt.PyJWTError as exc:
        raise SessionTokenInvalid("Runner session token is invalid.") from exc
    if (
        claims.get("workspace_id") != workspace_id
        or claims.get("runner_id") != runner_id
        or not _RESOURCE_ID_PATTERN.fullmatch(str(claims.get("workspace_id", "")))
        or not _RESOURCE_ID_PATTERN.fullmatch(str(claims.get("runner_id", "")))
        or claims.get("aud") != audience
        or claims.get("role") not in SESSION_ROLES
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"]
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
        or len(claims["jti"]) > 128
    ):
        raise SessionTokenInvalid("Runner session token is outside its configured scope.")
    return claims


def verify_workspace_session_with_signing_config(
    token: str,
    *,
    config: SessionSigningConfig,
    audience: str | None = None,
    workspace_id: str | None = None,
    runner_id: str | None = None,
) -> dict[str, Any]:
    """Verify a runner session against this control plane's active key set."""
    if not token or len(token) > 16_384:
        raise SessionTokenInvalid("Runner session token is invalid.")
    try:
        import jwt
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

        unverified = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
        token_audience = unverified.get("aud")
        token_workspace_id = unverified.get("workspace_id")
        token_runner_id = unverified.get("runner_id")
        expected_audience = audience or token_audience
        expected_workspace_id = workspace_id or token_workspace_id
        expected_runner_id = runner_id or token_runner_id
        if (
            not isinstance(expected_audience, str)
            or not isinstance(expected_workspace_id, str)
            or not isinstance(expected_runner_id, str)
            or expected_audience != expected_runner_id
            or (audience is not None and token_audience != audience)
            or (workspace_id is not None and token_workspace_id != workspace_id)
            or (runner_id is not None and token_runner_id != runner_id)
        ):
            raise SessionTokenInvalid("Runner session token is outside its configured scope.")
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        jwk = next(
            (item for item in config.public_jwks if item.get("kid") == key_id), None
        )
        if jwk is None:
            raise SessionTokenInvalid("Runner session token is invalid.")
        public_key = RSAPublicNumbers(
            _decode_base64url_integer(jwk["e"]),
            _decode_base64url_integer(jwk["n"]),
        ).public_key()
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=config.issuer,
            audience=expected_audience,
            leeway=SESSION_LEEWAY_SECONDS,
            options={
                "require": [
                    "iss", "sub", "aud", "workspace_id", "runner_id", "role",
                    "iat", "nbf", "exp", "jti",
                ]
            },
        )
    except SessionTokenInvalid:
        raise
    except (jwt.PyJWTError, TypeError, ValueError, KeyError) as exc:
        raise SessionTokenInvalid("Runner session token is invalid.") from exc

    if (
        claims.get("workspace_id") != expected_workspace_id
        or claims.get("runner_id") != expected_runner_id
        or not _RESOURCE_ID_PATTERN.fullmatch(str(claims.get("workspace_id", "")))
        or not _RESOURCE_ID_PATTERN.fullmatch(str(claims.get("runner_id", "")))
        or claims.get("aud") != expected_audience
        or claims.get("role") not in SESSION_ROLES
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"]
        or not isinstance(claims.get("jti"), str)
        or not claims["jti"]
        or len(claims["jti"]) > 128
    ):
        raise SessionTokenInvalid("Runner session token is outside its configured scope.")
    return claims
