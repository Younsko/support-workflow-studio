from __future__ import annotations

import base64
import json
from time import time
from types import SimpleNamespace

import pytest
from app.control_plane.auth import Principal
from app.control_plane.session_tokens import (
    SessionSigningConfig,
    SessionTokenConfigurationError,
    SessionTokenInvalid,
    issue_workspace_session,
    verify_workspace_session,
)


def _base64url_integer(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.fixture
def session_material(monkeypatch):
    jwt = pytest.importorskip("jwt")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    from cryptography.hazmat.primitives.serialization import NoEncryption

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("CONTROL_PLANE_SESSION_ISSUER", "https://control.example.test")
    monkeypatch.setenv("CONTROL_PLANE_SESSION_KEY_ID", "active-key")
    monkeypatch.setenv("CONTROL_PLANE_SESSION_SIGNING_PRIVATE_KEY_PEM", pem)
    monkeypatch.setenv("CONTROL_PLANE_SESSION_PREVIOUS_PUBLIC_KEYS_JSON", "[]")
    return jwt, key, SessionSigningConfig.from_env()


def test_workspace_session_is_short_lived_and_bound_to_workspace_and_runner(
    session_material,
) -> None:
    _, key, config = session_material
    now = int(time())
    principal = Principal(
        "idp|approver", "approver@example.test", expires_at=now + 600
    )
    token, expires_at = issue_workspace_session(
        principal,
        workspace_id="b" * 32,
        runner_id="a" * 32,
        role="Approver",
        config=config,
        now=now,
    )
    resolver = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )

    claims = verify_workspace_session(
        token,
        issuer=config.issuer,
        jwks_uri="https://control.example.test/.well-known/runner-session-jwks.json",
        audience="a" * 32,
        workspace_id="b" * 32,
        runner_id="a" * 32,
        jwks_client=resolver,
    )

    assert claims["sub"] == principal.subject
    assert claims["role"] == "Approver"
    assert claims["exp"] == expires_at
    assert claims["exp"] - claims["iat"] == 180
    assert claims["jti"]
    with pytest.raises(SessionTokenInvalid):
        verify_workspace_session(
            token,
            issuer=config.issuer,
            jwks_uri="https://control.example.test/.well-known/runner-session-jwks.json",
            audience="a" * 32,
            workspace_id="c" * 32,
            runner_id="a" * 32,
            jwks_client=resolver,
        )
    with pytest.raises(SessionTokenInvalid):
        verify_workspace_session(
            token,
            issuer=config.issuer,
            jwks_uri="https://control.example.test/.well-known/runner-session-jwks.json",
            audience="d" * 32,
            workspace_id="b" * 32,
            runner_id="a" * 32,
            jwks_client=resolver,
        )


def test_workspace_session_expiry_cannot_outlive_identity_token(session_material) -> None:
    _, _, config = session_material
    now = int(time())
    token, expires_at = issue_workspace_session(
        Principal("idp|operator", "operator@example.test", expires_at=now + 45),
        workspace_id="b" * 32,
        runner_id="a" * 32,
        role="Operator",
        config=config,
        now=now,
    )
    assert token
    assert expires_at == now + 45


def test_previous_public_key_overlap_is_supported_and_private_keys_are_rejected(
    monkeypatch, session_material
) -> None:
    _, _, _ = session_material
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    previous = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = previous.public_key().public_numbers()
    old_jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "previous-key",
        "n": _base64url_integer(numbers.n),
        "e": _base64url_integer(numbers.e),
    }
    monkeypatch.setenv(
        "CONTROL_PLANE_SESSION_PREVIOUS_PUBLIC_KEYS_JSON", json.dumps([old_jwk])
    )

    rotated = SessionSigningConfig.from_env()

    assert [key["kid"] for key in rotated.public_jwks] == ["active-key", "previous-key"]
    assert all("d" not in key for key in rotated.public_jwks)
    monkeypatch.setenv(
        "CONTROL_PLANE_SESSION_PREVIOUS_PUBLIC_KEYS_JSON",
        json.dumps([{**old_jwk, "d": "must-not-be-public"}]),
    )
    with pytest.raises(SessionTokenConfigurationError):
        SessionSigningConfig.from_env()


def test_workspace_session_requires_identity_expiry(session_material) -> None:
    _, _, config = session_material
    now = int(time())
    with pytest.raises(SessionTokenInvalid):
        issue_workspace_session(
            Principal("idp|operator", "operator@example.test"),
            workspace_id="b" * 32,
            runner_id="a" * 32,
            role="Operator",
            config=config,
            now=now,
        )
