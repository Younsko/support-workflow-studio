"""Opt-in per-request workspace session verification for a dedicated runner."""

from __future__ import annotations

import hmac
import ipaddress
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from .control_plane.session_tokens import SessionTokenInvalid, verify_workspace_session
from .runner_identity import RunnerIdentity

_OWNER_ADMIN = frozenset({"Owner", "Admin"})
_WORKFLOW_BUILDERS = frozenset({"Owner", "Admin", "Builder"})
_RUN_OPERATORS = frozenset({"Owner", "Admin", "Builder", "Operator"})
_APPROVERS = frozenset({"Owner", "Admin", "Approver"})
_ALL_ROLES = frozenset({"Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"})
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class RunnerAuthConfigurationError(RuntimeError):
    """Runner session authentication is enabled but misconfigured."""


@dataclass(frozen=True, slots=True)
class RunnerAuthConfig:
    identity: RunnerIdentity
    issuer: str
    jwks_uri: str
    control_plane_api_url: str
    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]
    audit_hash_key: bytes

    @classmethod
    def from_env(cls) -> RunnerAuthConfig | None:
        enabled = os.getenv("SUPPORT_RUNNER_AUTH_ENABLED", "").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return None

        try:
            identity = RunnerIdentity.from_env()
        except RuntimeError as exc:
            raise RunnerAuthConfigurationError(str(exc)) from exc
        if identity is None:
            raise RunnerAuthConfigurationError(
                "Runner authentication requires SUPPORT_WORKSPACE_ID and SUPPORT_RUNNER_ID."
            )

        issuer = os.getenv("SUPPORT_RUNNER_SESSION_ISSUER", "").strip()
        jwks_uri = os.getenv("SUPPORT_RUNNER_SESSION_JWKS_URI", "").strip()
        _require_https_url(issuer, "SUPPORT_RUNNER_SESSION_ISSUER")
        _require_https_url(jwks_uri, "SUPPORT_RUNNER_SESSION_JWKS_URI")
        control_plane_api_url = os.getenv("SUPPORT_CONTROL_PLANE_API_URL", "").strip()
        _require_api_base_url(control_plane_api_url)

        raw_hosts = os.getenv("SUPPORT_RUNNER_ALLOWED_HOSTS", "")
        hosts = frozenset(host.strip().lower() for host in raw_hosts.split(",") if host.strip())
        if not hosts or any(not _is_valid_host(host) for host in hosts):
            raise RunnerAuthConfigurationError(
                "SUPPORT_RUNNER_ALLOWED_HOSTS must list exact hostnames without ports or wildcards."
            )

        raw_origins = os.getenv("SUPPORT_RUNNER_ALLOWED_ORIGINS", "")
        origins = frozenset(
            _normalize_origin(origin.strip())
            for origin in raw_origins.split(",")
            if origin.strip()
        )
        if not origins:
            raise RunnerAuthConfigurationError(
                "SUPPORT_RUNNER_ALLOWED_ORIGINS must list exact browser origins."
            )
        audit_hash_secret = os.getenv("SUPPORT_RUNNER_AUDIT_HASH_KEY", "")
        if len(audit_hash_secret.encode("utf-8")) < 32:
            raise RunnerAuthConfigurationError(
                "SUPPORT_RUNNER_AUDIT_HASH_KEY must contain at least 32 UTF-8 bytes."
            )

        return cls(
            identity=identity,
            issuer=issuer,
            jwks_uri=jwks_uri,
            control_plane_api_url=control_plane_api_url.rstrip("/"),
            allowed_hosts=hosts,
            allowed_origins=origins,
            audit_hash_key=audit_hash_secret.encode("utf-8"),
        )

    def actor_id(self, subject: str) -> str:
        """Return a stable, workspace-local pseudonymous actor identifier."""
        return hmac.new(self.audit_hash_key, subject.encode("utf-8"), sha256).hexdigest()


def _require_https_url(value: str, name: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RunnerAuthConfigurationError(f"{name} must be a clean HTTPS URL.")


def _require_api_base_url(value: str) -> None:
    parsed = urlsplit(value)
    try:
        loopback = bool(parsed.hostname) and (
            ipaddress.ip_address(parsed.hostname).is_loopback
            if parsed.hostname != "localhost"
            else True
        )
    except ValueError:
        loopback = False
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
    ):
        raise RunnerAuthConfigurationError(
            "SUPPORT_CONTROL_PLANE_API_URL must be HTTPS, or HTTP on loopback, without a path or credentials."
        )


def _is_valid_host(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        labels = value.rstrip(".").split(".")
        return len(value) <= 253 and all(_HOST_LABEL.fullmatch(label) for label in labels)


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RunnerAuthConfigurationError(
            "SUPPORT_RUNNER_ALLOWED_ORIGINS must contain clean HTTP(S) origins."
        )
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.lower() == "localhost"
    if parsed.scheme != "https" and not is_loopback:
        raise RunnerAuthConfigurationError(
            "Non-loopback runner browser origins must use HTTPS."
        )
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def required_roles(method: str, path: str) -> frozenset[str] | None:
    """Return the minimum role set for a route; None means deny by default."""
    method = method.upper()
    normalized_path = path.rstrip("/") or "/"

    if method in {"GET", "HEAD"}:
        if normalized_path in {"/api/setup", "/api/health"}:
            return _OWNER_ADMIN
        if normalized_path in {
            "/api/cases",
            "/api/integrations/status",
            "/api/metrics",
            "/api/product-signals",
            "/api/v1/workflows",
        }:
            return _ALL_ROLES
        if re.fullmatch(r"/api/workflows/latest/[^/]+", normalized_path):
            return _ALL_ROLES
        if re.fullmatch(
            r"/api/workflows/[^/]+/(?:events|execution-steps)", normalized_path
        ):
            return _ALL_ROLES
        if re.fullmatch(r"/api/v1/workflows/[^/]+(?:/audit)?", normalized_path):
            return _ALL_ROLES
        return None
    if method == "OPTIONS":
        return frozenset()

    if normalized_path == "/api/setup" or normalized_path.startswith("/api/setup/"):
        return _OWNER_ADMIN
    if normalized_path == "/api/itsm/sync" or normalized_path == "/api/cases":
        return _RUN_OPERATORS
    if normalized_path == "/api/workflows":
        return _RUN_OPERATORS
    if re.fullmatch(r"/api/v1/workflows/[^/]+/runs", normalized_path):
        return _RUN_OPERATORS
    if re.fullmatch(r"/api/workflows/[^/]+/(?:decision|publish-ticket-reply)", normalized_path):
        return _APPROVERS
    if normalized_path.startswith("/api/v1/workflows"):
        return _WORKFLOW_BUILDERS

    return None


def authenticate_bearer(
    authorization: str | None,
    *,
    config: RunnerAuthConfig,
    jwks_client: Any | None = None,
) -> dict[str, Any]:
    if not authorization or len(authorization) > 16_500:
        raise SessionTokenInvalid("Runner session token is required.")
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise SessionTokenInvalid("Runner session token is required.")
    claims = verify_workspace_session(
        token.strip(),
        issuer=config.issuer,
        jwks_uri=config.jwks_uri,
        audience=config.identity.runner_id,
        workspace_id=config.identity.workspace_id,
        runner_id=config.identity.runner_id,
        jwks_client=jwks_client,
    )
    if (
        claims.get("workspace_id") != config.identity.workspace_id
        or claims.get("runner_id") != config.identity.runner_id
        or claims.get("role") not in _ALL_ROLES
        or not isinstance(claims.get("sub"), str)
        or not claims["sub"]
    ):
        raise SessionTokenInvalid("Runner session token is outside its configured scope.")
    return claims
