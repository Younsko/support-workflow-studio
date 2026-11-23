from __future__ import annotations

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
