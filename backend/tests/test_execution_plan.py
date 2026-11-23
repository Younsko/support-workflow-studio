from __future__ import annotations

import asyncio

import pytest
from app import execution_plan
from app.execution_plan import ExecutionPlanError, compile_published_workflow
from app.providers import ProviderConfig
from app.schemas import PublishedWorkflowRunRequest
from app.workflow_api import WorkflowRegistry
from app.workflow_definitions import (
    PublishedWorkflow,
    WorkflowConnection,
    WorkflowDefinition,
    WorkflowNode,
    publish_workflow,
)
from fastapi import HTTPException

NODE_TYPES = (
    "ticket.trigger",
    "support.triage",
    "knowledge.search",
    "ai.agent",
    "review.verify",
    "review.human_approval",
    "itsm.publish",
)


def starter(*, extra_edge: bool = False, model_ref: str = "demo:Synthetic workflow model") -> WorkflowDefinition:
    configs = [
        {"connection_ref": "jira-service-management"},
        {},
        {"source_ref": "local-approved-knowledge"},
        {
            "role": "Support analyst",
            "instructions": "Draft a response grounded in approved sources.",
            "provider": "demo",
            "model_ref": model_ref,
            "allowed_tools": ["knowledge.search"],
        },
        {},
        {"required_role": "Approver"},
        {"connection_ref": "jira-service-management", "operation": "public_reply"},
    ]
    nodes = [
        WorkflowNode(id=f"node-{index}", type=node_type, name=node_type, config=configs[index])
        for index, node_type in enumerate(NODE_TYPES)
    ]
    edges = [
        WorkflowConnection(id=f"edge-{index}", source=nodes[index].id, target=nodes[index + 1].id)
        for index in range(len(nodes) - 1)
    ]
    if extra_edge:
        edges.append(WorkflowConnection(id="edge-branch", source=nodes[1].id, target=nodes[3].id))
    return WorkflowDefinition(
        id="flow-support-intake",
        name="Support intake",
        version=1,
        status="draft",
        trigger_node_id=nodes[0].id,
        nodes=nodes,
        connections=edges,
    )


@pytest.fixture
def execution_bindings(monkeypatch: pytest.MonkeyPatch) -> ProviderConfig:
    monkeypatch.setenv("JSM_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JSM_USER_EMAIL", "synthetic@example.test")
    monkeypatch.setenv("JSM_API_TOKEN", "synthetic-token")
    monkeypatch.setenv("JSM_SERVICE_DESK_ID", "1")
    monkeypatch.setenv("JSM_QUEUE_ID", "2")
    monkeypatch.setattr(
        execution_plan,
        "knowledge_status",
        lambda: {"mode": "local", "available": True},
    )
    return ProviderConfig(
        provider="demo",
        model="Synthetic workflow model",
        api_key="",
        base_url="",
        timeout=1,
        max_calls=24,
    )


def test_compiles_exact_published_starter_flow(execution_bindings: ProviderConfig) -> None:
    published = publish_workflow(starter())

    plan = compile_published_workflow(
        published,
        expected_version=published.version,
        expected_content_hash=published.content_hash,
        provider=execution_bindings,
    )

    assert plan.workflow_id == "flow-support-intake"
    assert plan.version == 1
    assert plan.node_ids == tuple(f"node-{index}" for index in range(7))
    assert plan.agent_role == "Support analyst"


def test_rejects_branch_before_execution(execution_bindings: ProviderConfig) -> None:
    published = publish_workflow(starter(extra_edge=True))

    with pytest.raises(ExecutionPlanError, match="linear path"):
        compile_published_workflow(
            published,
            expected_version=published.version,
            expected_content_hash=published.content_hash,
            provider=execution_bindings,
        )


def test_rejects_model_binding_mismatch(execution_bindings: ProviderConfig) -> None:
    published = publish_workflow(starter(model_ref="demo:other-model"))

    with pytest.raises(ExecutionPlanError, match="model binding"):
        compile_published_workflow(
            published,
            expected_version=published.version,
            expected_content_hash=published.content_hash,
            provider=execution_bindings,
        )


def test_binding_fingerprint_changes_with_jira_destination(
    execution_bindings: ProviderConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = publish_workflow(starter())
    initial = compile_published_workflow(
        published,
        expected_version=published.version,
        expected_content_hash=published.content_hash,
        provider=execution_bindings,
    )
    monkeypatch.setenv("JSM_BASE_URL", "https://different.atlassian.net")
    changed = compile_published_workflow(
        published,
        expected_version=published.version,
        expected_content_hash=published.content_hash,
        provider=execution_bindings,
    )

    assert initial.bindings_fingerprint != changed.bindings_fingerprint


def test_rejects_stale_version_hash_or_corrupt_payload(execution_bindings: ProviderConfig) -> None:
    published = publish_workflow(starter())
    with pytest.raises(ExecutionPlanError, match="version changed"):
        compile_published_workflow(
            published,
            expected_version=2,
            expected_content_hash=published.content_hash,
            provider=execution_bindings,
        )
    with pytest.raises(ExecutionPlanError, match="hash does not match"):
        compile_published_workflow(
            published,
            expected_version=1,
            expected_content_hash="0" * 64,
            provider=execution_bindings,
        )
    corrupted = PublishedWorkflow(
        workflow_id=published.workflow_id,
        version=published.version,
        content_hash=published.content_hash,
        published_at=published.published_at,
        _payload=published._payload + " ",
    )
    with pytest.raises(ExecutionPlanError, match="integrity check"):
        compile_published_workflow(
            corrupted,
            expected_version=1,
            expected_content_hash=published.content_hash,
            provider=execution_bindings,
        )


def test_run_endpoint_compiles_snapshot_before_case_lookup(
    tmp_path, monkeypatch: pytest.MonkeyPatch, execution_bindings: ProviderConfig
) -> None:
    from app import main

    registry = WorkflowRegistry(tmp_path / "workflow.sqlite3")
    definition = starter()
    registry.save_draft(definition, expected_revision=None)
    published = registry.publish_draft(definition, expected_revision=1)
    monkeypatch.setattr(main, "configured_workflow_registry", lambda: registry)
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    monkeypatch.setenv("MODEL_NAME", execution_bindings.model)
    request = PublishedWorkflowRunRequest(
        case_id="missing-synthetic-case",
        workflow_version=published.version,
        workflow_content_hash=published.content_hash,
        idempotency_key="synthetic-run-request-1",
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.start_published_workflow(definition.id, request))

    # A successful compile reaches the local case lookup and stops there;
    # the test performs no Jira, knowledge, or model request.
    assert error.value.status_code == 404
