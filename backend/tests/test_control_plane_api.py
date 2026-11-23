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
