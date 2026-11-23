"""OIDC access-token validation for the metadata-only control plane."""

# FastAPI dependency calls in defaults are its standard injection syntax.
# ruff: noqa: B008

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class AuthenticationError(ValueError):
    """The supplied token is invalid or does not identify a verified user."""


class AuthenticationUnavailable(RuntimeError):
    """The configured identity provider could not be reached to verify a token."""


@dataclass(frozen=True, slots=True)
class OIDCConfig:
    issuer: str
    audience: str
    jwks_uri: str

    @classmethod
    def from_env(cls) -> OIDCConfig:
        issuer = os.getenv("CONTROL_PLANE_OIDC_ISSUER", "").strip().rstrip("/")
        audience = os.getenv("CONTROL_PLANE_OIDC_AUDIENCE", "").strip()
        jwks_uri = os.getenv("CONTROL_PLANE_OIDC_JWKS_URI", "").strip()
        if not issuer or not audience or not jwks_uri:
            raise RuntimeError(
                "The control-plane OIDC issuer, audience, and JWKS URI must be configured."
            )
        for value, label in ((issuer, "issuer"), (jwks_uri, "JWKS URI")):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise RuntimeError(
                    f"The control-plane OIDC {label} must be an HTTPS URL without credentials."
                )
        if len(audience) > 256:
            raise RuntimeError("The control-plane OIDC audience is too long.")
        return cls(issuer=issuer, audience=audience, jwks_uri=jwks_uri)


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    email: str
    expires_at: int | None = None


@lru_cache(maxsize=4)
def _jwks_client(uri: str) -> Any:
    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError(
            "OIDC validation requires the optional 'control-plane' package extra."
        ) from exc
    return jwt.PyJWKClient(uri, cache_jwk_set=True, lifespan=300, timeout=5)


def verify_access_token(
    token: str,
    config: OIDCConfig,
    *,
    jwks_client: Any | None = None,
) -> Principal:
    if not token or len(token) > 16_384:
        raise AuthenticationError("Bearer token is invalid.")
    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError(
            "OIDC validation requires the optional 'control-plane' package extra."
        ) from exc
    client = jwks_client or _jwks_client(config.jwks_uri)
    try:
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.issuer,
            audience=config.audience,
            leeway=30,
            options={"require": ["iss", "aud", "sub", "exp", "iat"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise AuthenticationUnavailable(
            "The identity provider key service is unavailable."
        ) from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Bearer token is invalid.") from exc

    subject = claims.get("sub")
    email = claims.get("email")
    if (
        not isinstance(subject, str)
        or not 1 <= len(subject) <= 255
        or not re.fullmatch(r"[A-Za-z0-9._:@|+-]+", subject)
        or not isinstance(email, str)
        or len(email) > 254
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        or claims.get("email_verified") is not True
    ):
        raise AuthenticationError("A verified email and stable subject are required.")
    return Principal(
        subject=subject,
        email=email.strip().casefold(),
        expires_at=int(claims["exp"]),
    )


_bearer = HTTPBearer(auto_error=False, scheme_name="OIDC bearer token")


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Sign-in is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_access_token(credentials.credentials, OIDCConfig.from_env())
    except AuthenticationUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
