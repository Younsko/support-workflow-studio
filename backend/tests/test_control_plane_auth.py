from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.control_plane.auth import (
    AuthenticationError,
    OIDCConfig,
    Principal,
    verify_access_token,
)


@pytest.fixture
def signing_material():
    jwt = pytest.importorskip("jwt")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    claims = {
        "iss": "https://identity.example.test/",
        "aud": "support-workflow-api",
        "sub": "provider|user-123",
        "email": "Reviewer@example.test",
        "email_verified": True,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    resolver = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )
    config = OIDCConfig(
        issuer="https://identity.example.test/",
        audience="support-workflow-api",
        jwks_uri="https://identity.example.test/.well-known/jwks.json",
    )
    return jwt, key, claims, token, resolver, config


def test_oidc_verifier_accepts_signed_token_with_exact_issuer_audience_and_verified_email(
    signing_material,
) -> None:
    _, _, _, token, resolver, config = signing_material

    principal = verify_access_token(token, config, jwks_client=resolver)

    assert principal == Principal(
        subject="provider|user-123",
        email="reviewer@example.test",
        expires_at=signing_material[2]["exp"],
    )


@pytest.mark.parametrize(
    "claim_updates",
    [
        {"aud": "another-service"},
        {"iss": "https://other.example.test"},
        {"email_verified": False},
        {"email": "not-an-email"},
    ],
)
def test_oidc_verifier_rejects_wrong_audience_issuer_or_unverified_identity(
    signing_material, claim_updates: dict[str, object]
) -> None:
    jwt, key, claims, _, resolver, config = signing_material
    token = jwt.encode(
        {**claims, **claim_updates}, key, algorithm="RS256", headers={"kid": "test-key"}
    )

    with pytest.raises(AuthenticationError):
        verify_access_token(token, config, jwks_client=resolver)


def test_oidc_verifier_rejects_expired_token(signing_material) -> None:
    jwt, key, claims, _, resolver, config = signing_material
    expired = jwt.encode(
        {**claims, "exp": int((datetime.now(UTC) - timedelta(minutes=2)).timestamp())},
        key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(AuthenticationError):
        verify_access_token(expired, config, jwks_client=resolver)


def test_oidc_verifier_rejects_tokens_with_unapproved_algorithm(
    signing_material,
) -> None:
    jwt, key, claims, _, resolver, config = signing_material
    token = jwt.encode(claims, key, algorithm="RS384", headers={"kid": "test-key"})

    with pytest.raises(AuthenticationError):
        verify_access_token(token, config, jwks_client=resolver)
