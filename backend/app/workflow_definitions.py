"""Typed, versioned workflow definitions and deterministic safety validation.

This module describes editable workflow graphs. It deliberately does not execute
nodes or call external systems; the runner can depend on this contract without
giving user-authored workflows arbitrary code execution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


NodeType = Literal[
    "ticket.trigger",
    "ticket.normalize",
    "support.triage",
    "knowledge.search",
    "ai.agent",
    "review.verify",
    "review.human_approval",
    "itsm.publish",
]

SUPPORTED_NODE_TYPES = frozenset(
    {
        "ticket.trigger",
        "ticket.normalize",
        "support.triage",
        "knowledge.search",
        "ai.agent",
        "review.verify",
        "review.human_approval",
        "itsm.publish",
    }
)

APPROVAL_ROLES = frozenset({"Owner", "Admin", "Approver"})
CONFIG_KEYS_BY_NODE: dict[str, frozenset[str]] = {
    "ticket.trigger": frozenset({"connection_ref"}),
    "ticket.normalize": frozenset(),
    "support.triage": frozenset(),
    "knowledge.search": frozenset({"source_ref"}),
    "ai.agent": frozenset(
        {"role", "instructions", "model_ref", "provider", "allowed_tools"}
    ),
    "review.verify": frozenset(),
    "review.human_approval": frozenset({"required_role"}),
    "itsm.publish": frozenset({"connection_ref", "operation"}),
}
SUPPORTED_AGENT_TOOLS = frozenset({"knowledge.search", "ticket.read"})


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)


class WorkflowDefinition(BaseModel):
    """A draft or an immutable versioned definition ready for publication."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    status: Literal["draft", "published"] = "draft"
    trigger_node_id: str = Field(min_length=1, max_length=80)
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=200)
    connections: list[WorkflowConnection] = Field(default_factory=list, max_length=500)

    @field_validator("id", "trigger_node_id")
    @classmethod
    def strip_identity_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


@dataclass(frozen=True, slots=True)
class WorkflowIssue:
    code: str
    message: str
    node_id: str | None = None
    connection_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublishedWorkflow:
    """An immutable serialized snapshot; callers receive fresh model copies."""

    workflow_id: str
    version: int
    content_hash: str
    published_at: str
    _payload: str

    def definition(self) -> WorkflowDefinition:
        return WorkflowDefinition.model_validate_json(self._payload)

    def fork_as_draft(self) -> WorkflowDefinition:
        published = self.definition()
        return published.model_copy(
            update={"version": published.version + 1, "status": "draft"}
        )


def validate_workflow(definition: WorkflowDefinition) -> list[WorkflowIssue]:
    """Return all deterministic graph and node-configuration issues."""

    issues: list[WorkflowIssue] = []
    by_id: dict[str, WorkflowNode] = {}
    duplicate_node_ids: set[str] = set()

    for node in definition.nodes:
        if node.id in by_id:
            duplicate_node_ids.add(node.id)
            issues.append(
                WorkflowIssue(
                    "duplicate_node_id",
                    f"Node ID '{node.id}' is used more than once.",
                    node_id=node.id,
                )
            )
        else:
            by_id[node.id] = node
        if node.type not in SUPPORTED_NODE_TYPES:
            issues.append(
                WorkflowIssue(
                    "unknown_node_type",
                    f"Node type '{node.type}' is not supported.",
                    node_id=node.id,
                )
            )
        issues.extend(_validate_node_config(node))

    trigger_nodes = [node for node in definition.nodes if node.type == "ticket.trigger"]
    if len(trigger_nodes) != 1:
        issues.append(
            WorkflowIssue(
                "trigger_count",
                "A workflow must define exactly one ticket trigger.",
            )
        )

    if definition.trigger_node_id in duplicate_node_ids:
        issues.append(
            WorkflowIssue(
                "ambiguous_trigger",
                "The trigger node ID is duplicated, so the graph entry is ambiguous.",
                node_id=definition.trigger_node_id,
            )
        )
    trigger = by_id.get(definition.trigger_node_id)
    if trigger is None:
        issues.append(
            WorkflowIssue(
                "missing_trigger",
                "The configured trigger node does not exist.",
                node_id=definition.trigger_node_id,
            )
        )
    elif trigger.type != "ticket.trigger":
        issues.append(
            WorkflowIssue(
                "invalid_trigger",
                "The configured workflow entry must be a ticket.trigger node.",
                node_id=trigger.id,
            )
        )

    connection_ids: set[str] = set()
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    reverse: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for connection in definition.connections:
        if connection.id in connection_ids:
            issues.append(
                WorkflowIssue(
                    "duplicate_connection_id",
                    f"Connection ID '{connection.id}' is used more than once.",
                    connection_id=connection.id,
                )
            )
        connection_ids.add(connection.id)
        source_exists = connection.source in by_id
        target_exists = connection.target in by_id
        if not source_exists or not target_exists:
            issues.append(
                WorkflowIssue(
                    "dangling_connection",
                    "Connection endpoints must reference existing nodes.",
                    connection_id=connection.id,
                )
            )
            continue
        if connection.source == connection.target:
            issues.append(
                WorkflowIssue(
                    "self_connection",
                    "A node cannot connect to itself.",
                    node_id=connection.source,
                    connection_id=connection.id,
                )
            )
        adjacency[connection.source].add(connection.target)
        reverse[connection.target].add(connection.source)

    if not duplicate_node_ids:
        cycle = _find_cycle(adjacency)
        if cycle:
            issues.append(
                WorkflowIssue(
                    "cycle",
                    "Workflow graphs must be acyclic; found cycle: " + " → ".join(cycle),
                )
            )

    if trigger is not None and trigger.id in adjacency:
        reachable = _reachable(trigger.id, adjacency)
        for node_id in sorted(set(by_id) - reachable):
            issues.append(
                WorkflowIssue(
                    "unreachable_node",
                    "Every workflow node must be reachable from the trigger.",
                    node_id=node_id,
                )
            )

    # Enforce the separate human approval gate on every route to a publish node.
    # If a publish remains reachable after removing approval nodes, there is a bypass.
    approval_ids = {
        node.id for node in by_id.values() if node.type == "review.human_approval"
    }
    publish_ids = {node.id for node in by_id.values() if node.type == "itsm.publish"}
    if publish_ids and trigger is not None:
        safe_graph = {
            node_id: {target for target in targets if target not in approval_ids}
            for node_id, targets in adjacency.items()
            if node_id not in approval_ids
        }
        reachable_without_approval = _reachable(trigger.id, safe_graph)
        for publish_id in sorted(publish_ids & reachable_without_approval):
            issues.append(
                WorkflowIssue(
                    "approval_bypass",
                    "Every path to an ITSM publish node must pass through human approval.",
                    node_id=publish_id,
                )
            )

    return issues


def publish_workflow(
    definition: WorkflowDefinition, *, published_at: datetime | None = None
) -> PublishedWorkflow:
    """Validate and freeze a published version as canonical JSON bytes-in-string."""

    if definition.status != "draft":
        raise ValueError("Only a draft workflow can be published.")
    issues = validate_workflow(definition)
    if issues:
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        raise ValueError(f"Workflow is invalid and cannot be published: {summary}")

    snapshot = definition.model_copy(update={"status": "published"})
    payload = snapshot.model_dump_json(exclude_none=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    timestamp = published_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("published_at must include a timezone.")
    return PublishedWorkflow(
        workflow_id=definition.id,
        version=definition.version,
        content_hash=digest,
        published_at=timestamp.astimezone(UTC).isoformat(),
        _payload=payload,
    )


def _validate_node_config(node: WorkflowNode) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    required_by_type: dict[str, tuple[str, ...]] = {
        "ticket.trigger": ("connection_ref",),
        "knowledge.search": ("source_ref",),
        "ai.agent": ("role", "instructions", "model_ref"),
        "itsm.publish": ("connection_ref", "operation"),
    }
    for key in required_by_type.get(node.type, ()):
        value = node.config.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                WorkflowIssue(
                    "missing_node_config",
                    f"Node type '{node.type}' requires a non-empty '{key}'.",
                    node_id=node.id,
                )
            )

    if node.type == "review.human_approval":
        role = node.config.get("required_role", "Approver")
        if role not in APPROVAL_ROLES:
            issues.append(
                WorkflowIssue(
                    "invalid_approval_role",
                    "Human approval must be assigned to Owner, Admin, or Approver.",
                    node_id=node.id,
                )
            )
    if node.type == "itsm.publish" and node.config.get("operation") not in {
        "public_reply"
    }:
        issues.append(
            WorkflowIssue(
                "unsafe_publish_operation",
                "The only supported ITSM publish operation is public_reply.",
                node_id=node.id,
            )
        )

    allowed_config_keys = CONFIG_KEYS_BY_NODE.get(node.type, frozenset())
    for key in node.config:
        normalized = "".join(char for char in key.casefold() if char.isalnum())
        sensitive_fragments = (
            "apikey",
            "secret",
            "password",
            "token",
            "credential",
            "authorization",
            "privatekey",
            "accesskey",
            "bearer",
        )
        if any(fragment in normalized for fragment in sensitive_fragments):
            issues.append(
                WorkflowIssue(
                    "inline_secret",
                    f"'{key}' looks sensitive and cannot be stored in workflow configuration.",
                    node_id=node.id,
                )
            )
        if key not in allowed_config_keys:
            issues.append(
                WorkflowIssue(
                    "unsupported_node_config",
                    f"'{key}' is not an allowed configuration field for '{node.type}'.",
                    node_id=node.id,
                )
            )
        value = node.config[key]
        if normalized.endswith("ref") and isinstance(value, str) and (
            value.startswith("unconfigured-") or value in {"", "unconfigured"}
        ):
            issues.append(
                WorkflowIssue(
                    "unresolved_reference",
                    f"'{key}' must refer to a configured connection, source, or model.",
                    node_id=node.id,
                )
            )
    if node.type == "ai.agent":
        tools = node.config.get("allowed_tools", [])
        if not isinstance(tools, list) or any(
            not isinstance(tool, str) or tool not in SUPPORTED_AGENT_TOOLS
            for tool in tools
        ):
            issues.append(
                WorkflowIssue(
                    "unsupported_agent_tool",
                    "Agent tools must be selected from the supported read-only tool list.",
                    node_id=node.id,
                )
            )
    try:
        json.dumps(node.config, allow_nan=False)
    except (TypeError, ValueError):
        issues.append(
            WorkflowIssue(
                "non_json_config",
                "Node configuration must contain only finite JSON-compatible values.",
                node_id=node.id,
            )
        )
    return issues


def _reachable(start: str, graph: dict[str, set[str]]) -> set[str]:
    if start not in graph:
        return set()
    visited: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(sorted(graph.get(current, set()) - visited, reverse=True))
    return visited


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node_id: str) -> list[str] | None:
        state[node_id] = 1
        stack.append(node_id)
        for target in sorted(graph.get(node_id, set())):
            if state.get(target, 0) == 0:
                found = visit(target)
                if found:
                    return found
            elif state.get(target) == 1:
                index = stack.index(target)
                return stack[index:] + [target]
        stack.pop()
        state[node_id] = 2
        return None

    for node_id in sorted(graph):
        if state.get(node_id, 0) == 0:
            found = visit(node_id)
            if found:
                return found
    return None
