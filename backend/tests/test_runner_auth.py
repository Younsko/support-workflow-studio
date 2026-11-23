from __future__ import annotations

import pytest
from app import main, runner_auth
from app.control_plane.session_tokens import SessionTokenInvalid
from app.runner_auth import (
    RunnerAuthConfig,
    RunnerAuthConfigurationError,
    required_roles,
)
from app.store import LocalStore
from app.workflow_api import WorkflowRegistry
from fastapi.testclient import TestClient


def _configure_runner_auth(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    workspace_id = "a" * 32
    runner_id = "b" * 32
    monkeypatch.setenv("SUPPORT_RUNNER_AUTH_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_WORKSPACE_ID", workspace_id)
    monkeypatch.setenv("SUPPORT_RUNNER_ID", runner_id)
    monkeypatch.setenv("SUPPORT_RUNNER_SESSION_ISSUER", "https://control.example")
    monkeypatch.setenv(
        "SUPPORT_RUNNER_SESSION_JWKS_URI",
        "https://control.example/.well-known/runner-session-jwks.json",
    )
    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_ORIGINS", "http://localhost:5173")
    return workspace_id, runner_id


def test_runner_auth_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPPORT_RUNNER_AUTH_ENABLED", raising=False)

    assert RunnerAuthConfig.from_env() is None


def test_runner_auth_configuration_requires_identity_and_explicit_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPPORT_RUNNER_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPPORT_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("SUPPORT_RUNNER_ID", raising=False)

    with pytest.raises(RunnerAuthConfigurationError, match="requires SUPPORT_WORKSPACE_ID"):
        RunnerAuthConfig.from_env()

    monkeypatch.setenv("SUPPORT_WORKSPACE_ID", "a" * 32)
    monkeypatch.setenv("SUPPORT_RUNNER_ID", "b" * 32)
    monkeypatch.setenv("SUPPORT_RUNNER_SESSION_ISSUER", "http://control.example")
    monkeypatch.setenv("SUPPORT_RUNNER_SESSION_JWKS_URI", "https://control.example/jwks")
    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_HOSTS", "runner.example")
    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_ORIGINS", "https://ui.example")

    with pytest.raises(RunnerAuthConfigurationError, match="ISSUER must be a clean HTTPS URL"):
        RunnerAuthConfig.from_env()


def test_runner_auth_configuration_rejects_wildcard_hosts_and_insecure_remote_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runner_auth(monkeypatch)
    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_HOSTS", "*.example.com")
    with pytest.raises(RunnerAuthConfigurationError, match="exact hostnames"):
        RunnerAuthConfig.from_env()

    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_HOSTS", "runner.example")
    monkeypatch.setenv("SUPPORT_RUNNER_ALLOWED_ORIGINS", "http://ui.example")
    with pytest.raises(RunnerAuthConfigurationError, match="must use HTTPS"):
        RunnerAuthConfig.from_env()


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/cases", {"Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"}),
        ("GET", "/api/setup", {"Owner", "Admin"}),
        ("PUT", "/api/setup", {"Owner", "Admin"}),
        ("POST", "/api/v1/workflows", {"Owner", "Admin", "Builder"}),
        ("POST", "/api/itsm/sync", {"Owner", "Admin", "Builder", "Operator"}),
        ("POST", "/api/workflows/run-1/decision", {"Owner", "Admin", "Approver"}),
        ("POST", "/api/workflows/run-1/publish-ticket-reply", {"Owner", "Admin", "Approver"}),
        ("GET", "/api/unclassified-read", None),
        ("POST", "/api/unclassified-write", None),
    ],
)
def test_runner_routes_use_deny_by_default_role_policies(
    method: str, path: str, expected: set[str] | None
) -> None:
    actual = required_roles(method, path)
    assert actual == (frozenset(expected) if expected is not None else None)


def test_authenticated_runner_routes_reject_missing_tokens_and_enforce_roles(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    workspace_id, runner_id = _configure_runner_auth(monkeypatch)
    state_store = LocalStore(tmp_path / "state.sqlite3")
    registry = WorkflowRegistry(tmp_path / "workflows.sqlite3")
    monkeypatch.setattr(main, "configured_store", lambda: state_store)
    monkeypatch.setattr(main, "configured_workflow_registry", lambda: registry)

    def verify(token: str, **kwargs: object) -> dict[str, object]:
        assert kwargs["audience"] == runner_id
        assert kwargs["workspace_id"] == workspace_id
        if token == "invalid":
            raise SessionTokenInvalid("invalid")
        role = {"viewer-token": "Viewer", "operator-token": "Operator"}.get(token)
        if role is None:
            return {
                "workspace_id": "c" * 32,
                "runner_id": runner_id,
                "role": "Owner",
                "sub": "subject-1",
                "jti": "jti-1",
            }
        return {
            "workspace_id": workspace_id,
            "runner_id": runner_id,
            "role": role,
            "sub": "subject-1",
            "jti": "jti-1",
        }

    monkeypatch.setattr(runner_auth, "verify_workspace_session", verify)

    with TestClient(main.app) as client:
        assert client.get("/api/cases").status_code == 401
        assert client.get(
            "/api/cases", headers={"Authorization": "Bearer invalid"}
        ).status_code == 401
        assert client.get(
            "/api/cases", headers={"Authorization": "Bearer wrong-workspace"}
        ).status_code == 401

        viewer = {"Authorization": "Bearer viewer-token"}
        assert client.get("/api/cases", headers=viewer).status_code == 200
        assert client.get("/api/setup", headers=viewer).status_code == 403
        assert client.post(
            "/api/cases",
            headers=viewer,
            json={
                "title": "Synthetic request",
                "requester": "Demo user",
                "body": "Synthetic test body",
                "impact": "One test account",
            },
        ).status_code == 403

        operator = {"Authorization": "Bearer operator-token"}
        created = client.post(
            "/api/cases",
            headers=operator,
            json={
                "title": "Synthetic request",
                "requester": "Demo user",
                "body": "Synthetic test body",
                "impact": "One test account",
            },
        )
        assert created.status_code == 201


def test_authenticated_runner_rejects_unlisted_host_and_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _configure_runner_auth(monkeypatch)
    monkeypatch.setattr(main, "configured_store", lambda: LocalStore(tmp_path / "state.sqlite3"))
    monkeypatch.setattr(
        main,
        "configured_workflow_registry",
        lambda: WorkflowRegistry(tmp_path / "workflows.sqlite3"),
    )
    monkeypatch.setattr(
        runner_auth,
        "verify_workspace_session",
        lambda token, **kwargs: {
            "workspace_id": "a" * 32,
            "runner_id": "b" * 32,
            "role": "Owner",
            "sub": "subject-1",
            "jti": "jti-1",
        },
    )
    headers = {"Authorization": "Bearer owner-token"}

    with TestClient(main.app) as client:
        assert client.get("/api/cases", headers={**headers, "Host": "evil.example"}).status_code == 403
        assert client.get(
            "/api/cases",
            headers={**headers, "Origin": "https://evil.example"},
        ).status_code == 403
