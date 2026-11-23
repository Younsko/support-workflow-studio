"""Fail-closed compiler for the one workflow topology currently executable.

Published graph definitions are not interpreted as arbitrary programs. This
compiler binds an immutable snapshot to the local connector/model setup and
rejects every topology or capability the runtime does not implement.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from .itsm import JiraConfig
from .knowledge import knowledge_status
from .providers import ProviderConfig
from .workflow_definitions import PublishedWorkflow, validate_workflow


class ExecutionPlanError(ValueError):
    """The published graph cannot be represented by the current runtime."""


SUPPORTED_TYPES = (
    "ticket.trigger",
    "support.triage",
    "knowledge.search",
    "ai.agent",
    "review.verify",
    "review.human_approval",
    "itsm.publish",
)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    workflow_id: str
    version: int
    content_hash: str
    node_ids: tuple[str, ...]
    agent_role: str
    agent_instructions: str
    model_ref: str
    bindings_fingerprint: str


def compile_published_workflow(
    published: PublishedWorkflow,
    *,
    expected_version: int,
    expected_content_hash: str,
    provider: ProviderConfig,
) -> ExecutionPlan:
    """Compile only the exact sequential support-response topology.

    This function is pure with respect to external services: callers can and
    should run it before acquiring data from Jira, knowledge, or a model.
    """
    if published.version != expected_version:
        raise ExecutionPlanError("Published workflow version changed; reload before running.")
    if published.content_hash != expected_content_hash:
        raise ExecutionPlanError("Published workflow hash does not match the selected version.")
    actual_hash = hashlib.sha256(published._payload.encode("utf-8")).hexdigest()
    if actual_hash != published.content_hash:
        raise ExecutionPlanError("Published workflow snapshot failed its integrity check.")

    definition = published.definition()
    if (
        definition.id != published.workflow_id
        or definition.version != published.version
        or definition.status != "published"
    ):
        raise ExecutionPlanError("Published workflow identity is inconsistent.")
    issues = validate_workflow(definition)
    if issues:
        raise ExecutionPlanError("Published workflow is invalid: " + "; ".join(i.message for i in issues))

    if tuple(node.type for node in definition.nodes) != SUPPORTED_TYPES:
        raise ExecutionPlanError(
            "Execution currently supports only the starter sequence: ticket, triage, knowledge, agent, verifier, approval, publish."
        )
    if len(definition.connections) != len(SUPPORTED_TYPES) - 1 or any(
        edge.source != definition.nodes[index].id
        or edge.target != definition.nodes[index + 1].id
        for index, edge in enumerate(definition.connections)
    ):
        raise ExecutionPlanError("Execution currently supports a single linear path without branches.")

    nodes = {node.type: node for node in definition.nodes}
    jira = JiraConfig.from_env()
    if not jira.configured:
        raise ExecutionPlanError("Configure Jira Service Management before publishing workflow runs.")
    for node_type in ("ticket.trigger", "itsm.publish"):
        if nodes[node_type].config.get("connection_ref") != "jira-service-management":
            raise ExecutionPlanError("Workflow Jira connection does not match the configured connector.")
    if nodes["itsm.publish"].config.get("operation") != "public_reply":
        raise ExecutionPlanError("Only the explicit public reply operation is currently executable.")

    knowledge = knowledge_status()
    expected_source = {
        "local": "local-approved-knowledge",
        "confluence": "confluence-approved-knowledge",
    }.get(str(knowledge.get("mode")))
    if not knowledge.get("available") or not expected_source:
        raise ExecutionPlanError("Configure an available approved knowledge source before running.")
    if nodes["knowledge.search"].config.get("source_ref") != expected_source:
        raise ExecutionPlanError("Workflow knowledge source does not match the configured source.")

    agent = nodes["ai.agent"].config
    expected_model_ref = f"{provider.provider}:{provider.model}"
    if agent.get("provider") != provider.provider or agent.get("model_ref") != expected_model_ref:
        raise ExecutionPlanError("Workflow model binding does not match the active provider and model.")
    if not provider.live and provider.provider != "demo":
        raise ExecutionPlanError("The selected model provider is not ready for workflow execution.")
    if agent.get("allowed_tools") != ["knowledge.search"]:
        raise ExecutionPlanError("The executable agent must have only the approved knowledge search tool.")
    role = agent.get("role")
    instructions = agent.get("instructions")
    if not isinstance(role, str) or not role.strip() or len(role) > 80:
        raise ExecutionPlanError("Agent role must contain 1–80 characters.")
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 240:
        raise ExecutionPlanError("Agent instructions must contain 1–240 characters.")
    if nodes["review.human_approval"].config.get("required_role", "Approver") != "Approver":
        raise ExecutionPlanError("The current local runtime requires the Approver review gate.")

    # Bind the compiled graph to non-secret connector destinations. Credential
    # rotation remains possible, while a tenant, queue, corpus or model target
    # change requires a fresh compile before any external read.
    knowledge_binding = {
        "mode": str(knowledge.get("mode")),
        "local_root": os.getenv("KNOWLEDGE_DIR", "").strip() if knowledge.get("mode") == "local" else "",
        "confluence_base_url": os.getenv("CONFLUENCE_BASE_URL", "").strip().rstrip("/") if knowledge.get("mode") == "confluence" else "",
        "confluence_space_keys": os.getenv("CONFLUENCE_SPACE_KEYS", "").strip() if knowledge.get("mode") == "confluence" else "",
    }
    binding_data = {
        "jira": {
            "base_url": jira.base_url,
            "service_desk_id": jira.service_desk_id,
            "queue_id": jira.queue_id,
        },
        "knowledge": knowledge_binding,
        "model": {
            "provider": provider.provider,
            "model": provider.model,
            "base_url": provider.base_url.rstrip("/"),
        },
    }
    bindings_fingerprint = hashlib.sha256(
        json.dumps(binding_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return ExecutionPlan(
        workflow_id=published.workflow_id,
        version=published.version,
        content_hash=published.content_hash,
        node_ids=tuple(node.id for node in definition.nodes),
        agent_role=role.strip(),
        agent_instructions=instructions.strip(),
        model_ref=expected_model_ref,
        bindings_fingerprint=bindings_fingerprint,
    )
