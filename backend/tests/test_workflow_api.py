from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.workflow_api import WorkflowRegistry
from app.workflow_definitions import WorkflowDefinition, WorkflowNode, validate_workflow


def draft() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="flow-support-intake",
        name="Support intake",
        version=1,
        status="draft",
        trigger_node_id="ticket",
        nodes=[
            WorkflowNode(
                id="ticket",
                type="ticket.trigger",
                name="Ticket received",
                config={"connection_ref": "jsm-support"},
            )
        ],
    )


def test_workflow_draft_survives_registry_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    registry = WorkflowRegistry(path)
    definition = draft()

    assert registry.save_draft(definition, expected_revision=None) == 1
    reopened = WorkflowRegistry(path)
    bundle = reopened.get_bundle(definition.id)

    assert bundle is not None
    assert bundle["draft"]["revision"] == 1
    assert bundle["draft"]["definition"] == definition
    assert bundle["published"] == []


def test_registry_rejects_stale_draft_revision(tmp_path: Path) -> None:
    registry = WorkflowRegistry(tmp_path / "state.sqlite3")
    definition = draft()
    registry.save_draft(definition, expected_revision=None)

    with pytest.raises(ValueError, match="revision changed"):
        registry.save_draft(definition, expected_revision=8)


def test_publish_is_immutable_and_fork_creates_next_draft(tmp_path: Path) -> None:
    registry = WorkflowRegistry(tmp_path / "state.sqlite3")
    definition = draft()
    registry.save_draft(definition, expected_revision=None)

    published = registry.publish_draft(definition, expected_revision=1)
    bundle = registry.get_bundle(definition.id)

    assert published.version == 1
    assert bundle is not None
    assert bundle["draft"] is None
    assert len(bundle["published"]) == 1
    assert bundle["published"][0]["definition"].status == "published"

    forked, revision, created = registry.fork_latest(definition.id)
    assert forked.version == 2
    assert forked.status == "draft"
    assert revision == 1
    assert created is True
    assert registry.get_bundle(definition.id)["published"][0]["definition"].version == 1

    existing, existing_revision, created = registry.fork_latest(definition.id)
    assert existing == forked
    assert existing_revision == revision
    assert created is False


def test_publish_requires_exact_saved_snapshot(tmp_path: Path) -> None:
    registry = WorkflowRegistry(tmp_path / "state.sqlite3")
    definition = draft()
    registry.save_draft(definition, expected_revision=None)
    changed = definition.model_copy(update={"name": "Changed after save"})

    with pytest.raises(ValueError, match="match the saved draft"):
        registry.publish_draft(changed, expected_revision=1)


def test_unconfigured_connection_reference_is_not_publishable() -> None:
    definition = draft().model_copy(
        update={
            "nodes": [
                WorkflowNode(
                    id="ticket",
                    type="ticket.trigger",
                    name="Ticket received",
                    config={"connection_ref": "unconfigured-jsm-connection"},
                )
            ]
        }
    )

    assert "unresolved_reference" in {
        issue.code for issue in validate_workflow(definition)
    }


def test_local_workflow_http_api_saves_validates_and_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPPORT_WORKFLOW_DB_PATH", str(tmp_path / "api.sqlite3"))
    from app.main import app

    definition = draft().model_dump(mode="json")
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/workflows", json={"definition": definition}
        )
        assert created.status_code == 201
        assert created.json()["revision"] == 1

        validation = client.post(
            "/api/v1/workflows/flow-support-intake/validate", json=definition
        )
        assert validation.status_code == 200
        assert validation.json()["valid"] is True

        published = client.post(
            "/api/v1/workflows/flow-support-intake/publish",
            json={"definition": definition, "expected_revision": 1},
        )
        assert published.status_code == 200
        assert published.json()["definition"]["status"] == "published"
        assert published.json()["execution"] == "not_connected"

        bundle = client.get("/api/v1/workflows/flow-support-intake")
        assert bundle.status_code == 200
        assert bundle.json()["draft"] is None
        assert bundle.json()["published"][0]["definition"]["version"] == 1

        audit = client.get("/api/v1/workflows/flow-support-intake/audit")
        assert audit.status_code == 200
        assert [event["event_type"] for event in audit.json()] == [
            "draft.created",
            "version.published",
        ]
        assert all("payload" not in event for event in audit.json())

        forked = client.post("/api/v1/workflows/flow-support-intake/fork")
        assert forked.status_code == 200
        assert forked.json()["definition"]["version"] == 2
        assert forked.json()["created"] is True
        second_fork = client.post("/api/v1/workflows/flow-support-intake/fork")
        assert second_fork.status_code == 409
        assert second_fork.json()["detail"]["code"] == "draft_already_exists"
        assert second_fork.json()["detail"]["revision"] == 1
        audit = client.get("/api/v1/workflows/flow-support-intake/audit")
        assert audit.json()[-1]["event_type"] == "draft.forked"


def test_workflow_draft_update_cors_preflight_allows_put() -> None:
    from app.main import app

    response = TestClient(app).options(
        "/api/v1/workflows/flow-support-intake/draft",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
