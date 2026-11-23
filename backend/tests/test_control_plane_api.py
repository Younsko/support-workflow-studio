from __future__ import annotations

from time import time

import pytest
from app.control_plane.api import create_control_plane_app, get_control_plane_store
from app.control_plane.auth import Principal, current_principal
from fastapi.testclient import TestClient


def test_control_plane_api_requires_oidc_bearer_before_workspace_access(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CONTROL_PLANE_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("CONTROL_PLANE_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("CONTROL_PLANE_OIDC_JWKS_URI", raising=False)

    client = TestClient(create_control_plane_app())
    me = client.get("/api/v1/me")
    create = client.post("/api/v1/workspaces", json={"name": "Support Team"})

    assert me.status_code == 401
    assert create.status_code == 401
    assert client.get("/health/live").status_code == 200
    assert client.get("/docs").status_code == 404


def test_public_auth_config_contains_only_browser_safe_oidc_values(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_OIDC_ISSUER", "https://login.example.test/")
    monkeypatch.setenv("CONTROL_PLANE_OIDC_AUDIENCE", "private-api-audience")
    monkeypatch.setenv(
        "CONTROL_PLANE_OIDC_JWKS_URI", "https://login.example.test/keys"
    )
    monkeypatch.setenv("CONTROL_PLANE_OIDC_CLIENT_ID", "public-browser-client")

    response = TestClient(create_control_plane_app()).get("/api/v1/auth/config")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "issuer": "https://login.example.test",
        "audience": "private-api-audience",
        "client_id": "public-browser-client",
    }
    assert "jwks" not in response.text.casefold()


def test_control_plane_cors_requires_an_explicit_browser_origin(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_PLANE_ALLOWED_ORIGINS", "https://support.example")
    client = TestClient(create_control_plane_app())

    allowed = client.options(
        "/api/v1/workspaces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/runners/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/session",
        headers={
            "Origin": "https://support.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    blocked = client.options(
        "/api/v1/workspaces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/runners/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/session",
        headers={
            "Origin": "https://unlisted.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://support.example"
    assert blocked.status_code == 400
    assert "access-control-allow-origin" not in blocked.headers


@pytest.mark.parametrize("origin", ["*", "http://support.example", "https://support.example/path"])
def test_control_plane_cors_rejects_wildcard_and_non_origin_values(
    monkeypatch, origin: str
) -> None:
    monkeypatch.setenv("CONTROL_PLANE_ALLOWED_ORIGINS", origin)
    with pytest.raises(RuntimeError, match="exact HTTPS origins"):
        create_control_plane_app()


def test_runner_session_is_workspace_and_runner_bound_and_non_cacheable(monkeypatch) -> None:
    jwt = pytest.importorskip("jwt")
    pytest.importorskip("cryptography.hazmat.primitives.serialization")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import NoEncryption

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("CONTROL_PLANE_SESSION_ISSUER", "https://control.example.test")
    monkeypatch.setenv("CONTROL_PLANE_SESSION_KEY_ID", "test-key-1")
    monkeypatch.setenv("CONTROL_PLANE_SESSION_SIGNING_PRIVATE_KEY_PEM", private_pem)
    monkeypatch.setenv("CONTROL_PLANE_SESSION_PREVIOUS_PUBLIC_KEYS_JSON", "[]")

    expires_at = int(time()) + 600
    app = create_control_plane_app()
    app.dependency_overrides[current_principal] = lambda: Principal(
        subject="oidc|approver", email="approver@example.test", expires_at=expires_at
    )

    class StoreStub:
        def authorize_runner_session(self, workspace_id, runner_id, subject):
            assert workspace_id == "b" * 32
            assert runner_id == "a" * 32
            assert subject == "oidc|approver"
            return "Approver"

    app.dependency_overrides[get_control_plane_store] = lambda: StoreStub()
    response = TestClient(app).post(
        f"/api/v1/workspaces/{'b' * 32}/runners/{'a' * 32}/session",
        json={"role": "Owner", "workspace_id": "c" * 32},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    access_token = response.json()["access_token"]
    claims = jwt.decode(
        access_token,
        private_key.public_key(),
        algorithms=["RS256"],
        issuer="https://control.example.test",
        audience="a" * 32,
    )
    assert claims["sub"] == "oidc|approver"
    assert claims["workspace_id"] == "b" * 32
    assert claims["runner_id"] == "a" * 32
    assert claims["role"] == "Approver"
    assert 0 < claims["exp"] - claims["iat"] <= 180
    assert response.json()["expires_at"] == claims["exp"]
    assert "private" not in TestClient(app).get(
        "/.well-known/runner-session-jwks.json"
    ).text.casefold()


def test_audit_limit_is_rejected_at_the_http_boundary() -> None:
    app = create_control_plane_app()
    app.dependency_overrides[current_principal] = lambda: Principal(
        subject="subject-1", email="person@example.test"
    )
    app.dependency_overrides[get_control_plane_store] = lambda: object()
    client = TestClient(app)

    too_small = client.get("/api/v1/workspaces/ws_123/audit?limit=0")
    too_large = client.get("/api/v1/workspaces/ws_123/audit?limit=501")

    assert too_small.status_code == 422
    assert too_large.status_code == 422


def test_runner_enrollment_and_heartbeat_mark_credentials_as_non_cacheable() -> None:
    app = create_control_plane_app()
    app.dependency_overrides[current_principal] = lambda: Principal(
        subject="subject-1", email="person@example.test"
    )

    class StoreStub:
        def __init__(self) -> None:
            self.token: str | None = None

        def create_runner_enrollment(
            self,
            workspace_id: str,
            principal: Principal,
            *,
            name: str,
            token: str,
            expires_at,
        ) -> dict[str, object]:
            self.token = token
            return {
                "id": "a" * 32,
                "workspace_id": workspace_id,
                "name": name,
                "expires_at": expires_at.isoformat(),
            }

        def record_runner_heartbeat(
            self, runner_id: str, token: str
        ) -> dict[str, object]:
            assert token == self.token
            return {"runner_id": runner_id, "last_heartbeat_at": "now"}

    store = StoreStub()
    app.dependency_overrides[get_control_plane_store] = lambda: store
    client = TestClient(app)

    enrollment = client.post(
        f"/api/v1/workspaces/{'b' * 32}/runners", json={"name": "Private runner"}
    )
    token = enrollment.json()["token"]
    heartbeat = client.post(
        f"/api/v1/runners/{'a' * 32}/heartbeat",
        headers={"X-Runner-Token": token},
    )

    assert enrollment.status_code == 201
    assert len(token) >= 32
    assert enrollment.headers["cache-control"] == "no-store"
    assert heartbeat.status_code == 200
    assert heartbeat.headers["cache-control"] == "no-store"
    invalid_expiry = client.post(
        f"/api/v1/workspaces/{'b' * 32}/runners",
        json={"name": "Too long", "expires_in_days": 91},
    )
    assert invalid_expiry.status_code == 422
