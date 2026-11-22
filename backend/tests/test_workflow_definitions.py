from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.workflow_definitions import (
    WorkflowDefinition,
    WorkflowConnection,
    publish_workflow,
    validate_workflow,
)


def valid_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "support-default",
            "name": "Support intake",
            "version": 1,
            "trigger_node_id": "trigger",
            "nodes": [
                {
                    "id": "trigger",
                    "type": "ticket.trigger",
                    "name": "New ticket",
                    "config": {"connection_ref": "jsm-main"},
                },
                {
                    "id": "normalize",
                    "type": "ticket.normalize",
                    "name": "Normalize",
                },
                {
                    "id": "knowledge",
                    "type": "knowledge.search",
                    "name": "Search knowledge",
                    "config": {"source_ref": "kb-main"},
                },
                {
                    "id": "agent",
                    "type": "ai.agent",
                    "name": "Draft response",
                    "config": {
                        "role": "Support analyst",
                        "instructions": "Draft a sourced support response.",
                        "model_ref": "local-model",
                        "allowed_tools": ["knowledge.search"],
                    },
                },
                {
                    "id": "verify",
                    "type": "review.verify",
                    "name": "Verify evidence",
                },
                {
                    "id": "approve",
                    "type": "review.human_approval",
                    "name": "Human approval",
                },
                {
                    "id": "publish",
                    "type": "itsm.publish",
                    "name": "Publish reply",
                    "config": {
                        "connection_ref": "jsm-main",
                        "operation": "public_reply",
                    },
                },
            ],
            "connections": [
                {"id": "e1", "source": "trigger", "target": "normalize"},
                {"id": "e2", "source": "normalize", "target": "knowledge"},
                {"id": "e3", "source": "knowledge", "target": "agent"},
                {"id": "e4", "source": "agent", "target": "verify"},
                {"id": "e5", "source": "verify", "target": "approve"},
                {"id": "e6", "source": "approve", "target": "publish"},
            ],
        }
    )


def test_valid_reference_workflow_has_no_issues() -> None:
    assert validate_workflow(valid_workflow()) == []


def test_duplicate_node_id_is_reported() -> None:
    workflow = valid_workflow()
    duplicate = workflow.nodes[1].model_copy(update={"id": "trigger"})
    workflow = workflow.model_copy(update={"nodes": [*workflow.nodes, duplicate]})

    assert "duplicate_node_id" in {issue.code for issue in validate_workflow(workflow)}


def test_dangling_connection_is_reported() -> None:
    workflow = valid_workflow()
    connection = workflow.connections[0].model_copy(
        update={"target": "missing-node"}
    )
    workflow = workflow.model_copy(update={"connections": [connection]})

    assert "dangling_connection" in {issue.code for issue in validate_workflow(workflow)}


def test_cycle_is_reported() -> None:
    workflow = valid_workflow()
    connections = [
        *workflow.connections,
        WorkflowConnection(id="cycle", source="publish", target="trigger"),
    ]
    workflow = workflow.model_copy(update={"connections": connections})

    assert "cycle" in {issue.code for issue in validate_workflow(workflow)}


def test_unknown_node_type_is_reported() -> None:
    workflow = valid_workflow()
    node = workflow.nodes[1].model_copy(update={"type": "arbitrary.python"})
    workflow = workflow.model_copy(
        update={"nodes": [workflow.nodes[0], node, *workflow.nodes[2:]]}
    )

    assert "unknown_node_type" in {issue.code for issue in validate_workflow(workflow)}


def test_sensitive_and_unknown_configuration_fields_are_rejected() -> None:
    workflow = valid_workflow()
    agent = workflow.nodes[3].model_copy(
        update={
            "config": {
                **workflow.nodes[3].config,
                "client_secret": "synthetic-never-a-real-secret",
            }
        }
    )
    workflow = workflow.model_copy(
        update={"nodes": [*workflow.nodes[:3], agent, *workflow.nodes[4:]]}
    )

    codes = {issue.code for issue in validate_workflow(workflow)}
    assert "inline_secret" in codes
    assert "unsupported_node_config" in codes


def test_agent_tool_permissions_are_allowlisted() -> None:
    workflow = valid_workflow()
    agent = workflow.nodes[3].model_copy(
        update={"config": {**workflow.nodes[3].config, "allowed_tools": ["shell.run"]}}
    )
    workflow = workflow.model_copy(
        update={"nodes": [*workflow.nodes[:3], agent, *workflow.nodes[4:]]}
    )

    assert "unsupported_agent_tool" in {
        issue.code for issue in validate_workflow(workflow)
    }


def test_publish_freezes_a_snapshot_and_fork_advances_version() -> None:
    draft = valid_workflow()
    published = publish_workflow(
        draft, published_at=datetime(2026, 11, 22, tzinfo=UTC)
    )
    expected_hash = published.content_hash

    draft.name = "Renamed draft"
    fresh_snapshot = published.definition()
    fresh_snapshot.name = "Mutated copy"
    fork = published.fork_as_draft()

    assert published.content_hash == expected_hash
    assert published.definition().name == "Support intake"
    assert published.definition().status == "published"
    assert fork.name == "Support intake"
    assert fork.version == 2
    assert fork.status == "draft"


def test_publish_rejects_a_route_that_bypasses_human_approval() -> None:
    workflow = valid_workflow()
    connections = [
        *workflow.connections,
        WorkflowConnection(id="bypass", source="verify", target="publish"),
    ]
    workflow = workflow.model_copy(update={"connections": connections})

    with pytest.raises(ValueError, match="approval_bypass"):
        publish_workflow(workflow)
