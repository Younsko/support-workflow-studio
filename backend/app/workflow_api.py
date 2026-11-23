"""Local API and durable registry for editable workflow definitions.

These endpoints are intentionally part of the loopback-only prototype. They
provide versioned workflow editing but do not imply hosted authentication,
tenant isolation, runner execution, or production persistence.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from .store import configured_store_path
from .workflow_definitions import (
    PublishedWorkflow,
    WorkflowDefinition,
    publish_workflow,
    validate_workflow,
)


class DraftWrite(BaseModel):
    definition: WorkflowDefinition
    expected_revision: int | None = Field(default=None, ge=1)


class WorkflowRegistry:
    """SQLite-backed local registry with immutable published snapshots."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_drafts (
                    workflow_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_versions (
                    workflow_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (workflow_id, version)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_audit_events (
                    event_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    revision INTEGER,
                    occurred_at TEXT NOT NULL,
                    content_hash TEXT
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def list_workflows(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT ids.workflow_id,
                          COALESCE(d.payload, v.payload) AS payload,
                          d.version AS draft_version,
                          d.revision AS draft_revision,
                          d.updated_at AS draft_updated_at,
                          v.version AS published_version,
                          v.published_at AS published_at
                   FROM (
                       SELECT workflow_id FROM workflow_drafts
                       UNION
                       SELECT workflow_id FROM workflow_versions
                   ) AS ids
                   LEFT JOIN workflow_drafts d ON d.workflow_id = ids.workflow_id
                   LEFT JOIN workflow_versions v ON v.workflow_id = ids.workflow_id
                     AND v.version = (
                       SELECT MAX(v2.version) FROM workflow_versions v2
                       WHERE v2.workflow_id = ids.workflow_id
                     )
                   ORDER BY ids.workflow_id"""
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            definition = WorkflowDefinition.model_validate_json(row["payload"])
            results.append(
                {
                    "id": row["workflow_id"],
                    "name": definition.name,
                    "draft_version": row["draft_version"],
                    "draft_revision": row["draft_revision"],
                    "draft_updated_at": row["draft_updated_at"],
                    "published_version": row["published_version"],
                    "published_at": row["published_at"],
                }
            )
        return results

    def get_bundle(self, workflow_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            draft = connection.execute(
                "SELECT version, revision, payload, updated_at FROM workflow_drafts WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            versions = connection.execute(
                "SELECT version, content_hash, published_at, payload FROM workflow_versions WHERE workflow_id = ? ORDER BY version DESC",
                (workflow_id,),
            ).fetchall()
        if draft is None and not versions:
            return None
        return {
            "id": workflow_id,
            "draft": (
                {
                    "definition": WorkflowDefinition.model_validate_json(draft["payload"]),
                    "revision": draft["revision"],
                    "updated_at": draft["updated_at"],
                }
                if draft is not None
                else None
            ),
            "published": [
                {
                    "definition": WorkflowDefinition.model_validate_json(row["payload"]),
                    "content_hash": row["content_hash"],
                    "published_at": row["published_at"],
                }
                for row in versions
            ],
        }

    def get_published(self, workflow_id: str, version: int) -> PublishedWorkflow | None:
        """Load one frozen published snapshot and verify its stored digest."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT content_hash, published_at, payload FROM workflow_versions WHERE workflow_id = ? AND version = ?",
                (workflow_id, version),
            ).fetchone()
        if row is None:
            return None
        digest = hashlib.sha256(row["payload"].encode("utf-8")).hexdigest()
        if digest != row["content_hash"]:
            raise ValueError("Published workflow snapshot failed its integrity check.")
        definition = WorkflowDefinition.model_validate_json(row["payload"])
        if (
            definition.id != workflow_id
            or definition.version != version
            or definition.status != "published"
        ):
            raise ValueError("Published workflow identity does not match its registry key.")
        return PublishedWorkflow(
            workflow_id=workflow_id,
            version=version,
            content_hash=digest,
            published_at=row["published_at"],
            _payload=row["payload"],
        )

    def list_audit_events(self, workflow_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT event_id, event_type, version, revision, occurred_at, content_hash
                   FROM workflow_audit_events WHERE workflow_id = ?
                   ORDER BY occurred_at, event_id""",
                (workflow_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        *,
        workflow_id: str,
        event_type: str,
        version: int,
        revision: int | None,
        content_hash: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO workflow_audit_events(
                event_id, workflow_id, event_type, version, revision, occurred_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid4().hex,
                workflow_id,
                event_type,
                version,
                revision,
                datetime.now(UTC).isoformat(),
                content_hash,
            ),
        )

    def save_draft(
        self, definition: WorkflowDefinition, *, expected_revision: int | None
    ) -> int:
        if definition.status != "draft":
            raise ValueError("Only draft workflow definitions can be saved.")
        payload = definition.model_dump_json(exclude_none=False)
        timestamp = datetime.now(UTC).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                "SELECT MAX(version) AS version FROM workflow_versions WHERE workflow_id = ?",
                (definition.id,),
            ).fetchone()["version"]
            required_version = (latest or 0) + 1
            if definition.version != required_version:
                raise ValueError(
                    f"Draft version must be the next version ({required_version})."
                )
            current = connection.execute(
                "SELECT revision FROM workflow_drafts WHERE workflow_id = ?",
                (definition.id,),
            ).fetchone()
            if current is None:
                if expected_revision is not None:
                    raise ValueError("This draft no longer exists; reload the workflow.")
                revision = 1
                connection.execute(
                    "INSERT INTO workflow_drafts(workflow_id, version, revision, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (definition.id, definition.version, revision, payload, timestamp),
                )
                event_type = "draft.created"
            else:
                if expected_revision != current["revision"]:
                    raise ValueError("Draft revision changed; reload before saving.")
                revision = current["revision"] + 1
                connection.execute(
                    "UPDATE workflow_drafts SET version = ?, revision = ?, payload = ?, updated_at = ? WHERE workflow_id = ?",
                    (definition.version, revision, payload, timestamp, definition.id),
                )
                event_type = "draft.saved"
            self._append_audit(
                connection,
                workflow_id=definition.id,
                event_type=event_type,
                version=definition.version,
                revision=revision,
            )
            connection.commit()
            return revision
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def publish_draft(
        self, definition: WorkflowDefinition, *, expected_revision: int
    ) -> PublishedWorkflow:
        frozen = publish_workflow(definition)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision, payload FROM workflow_drafts WHERE workflow_id = ?",
                (definition.id,),
            ).fetchone()
            if current is None or current["revision"] != expected_revision:
                raise ValueError("Draft revision changed; reload before publishing.")
            if current["payload"] != definition.model_dump_json(exclude_none=False):
                raise ValueError("Published content must match the saved draft exactly.")
            connection.execute(
                "INSERT INTO workflow_versions(workflow_id, version, content_hash, published_at, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    frozen.workflow_id,
                    frozen.version,
                    frozen.content_hash,
                    frozen.published_at,
                    frozen.definition().model_dump_json(exclude_none=False),
                ),
            )
            self._append_audit(
                connection,
                workflow_id=definition.id,
                event_type="version.published",
                version=frozen.version,
                revision=expected_revision,
                content_hash=frozen.content_hash,
            )
            connection.execute(
                "DELETE FROM workflow_drafts WHERE workflow_id = ? AND revision = ?",
                (definition.id, expected_revision),
            )
            connection.commit()
            return frozen
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fork_latest(self, workflow_id: str) -> tuple[WorkflowDefinition, int, bool]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT revision, payload FROM workflow_drafts WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return (
                    WorkflowDefinition.model_validate_json(existing["payload"]),
                    existing["revision"],
                    False,
                )
            latest = connection.execute(
                "SELECT payload FROM workflow_versions WHERE workflow_id = ? ORDER BY version DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
            if latest is None:
                raise KeyError(workflow_id)
            published = WorkflowDefinition.model_validate_json(latest["payload"])
            draft = published.model_copy(
                update={"version": published.version + 1, "status": "draft"}
            )
            revision = 1
            connection.execute(
                "INSERT INTO workflow_drafts(workflow_id, version, revision, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    workflow_id,
                    draft.version,
                    revision,
                    draft.model_dump_json(exclude_none=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._append_audit(
                connection,
                workflow_id=workflow_id,
                event_type="draft.forked",
                version=draft.version,
                revision=revision,
            )
            connection.commit()
            return draft, revision, True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


@lru_cache(maxsize=4)
def _workflow_registry_for_path(path: str) -> WorkflowRegistry:
    return WorkflowRegistry(Path(path))


def configured_workflow_registry() -> WorkflowRegistry:
    return _workflow_registry_for_path(str(configured_store_path()))


router = APIRouter(prefix="/api/v1/workflows", tags=["workflow definitions"])


@router.get("")
def list_workflows() -> list[dict[str, Any]]:
    return configured_workflow_registry().list_workflows()


@router.post("", status_code=201)
def create_workflow_draft(write: DraftWrite, response: Response) -> dict[str, Any]:
    definition = write.definition
    if definition.version != 1:
        raise HTTPException(status_code=409, detail="A new workflow must start at version 1.")
    try:
        revision = configured_workflow_registry().save_draft(
            definition, expected_revision=write.expected_revision
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["ETag"] = f'"{revision}"'
    return {"definition": definition, "revision": revision, "saved": True}


@router.get("/{workflow_id}")
def get_workflow_bundle(workflow_id: str) -> dict[str, Any]:
    bundle = configured_workflow_registry().get_bundle(workflow_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return bundle


@router.get("/{workflow_id}/audit")
def list_workflow_audit_events(workflow_id: str) -> list[dict[str, Any]]:
    if configured_workflow_registry().get_bundle(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return configured_workflow_registry().list_audit_events(workflow_id)


@router.put("/{workflow_id}/draft")
def update_workflow_draft(workflow_id: str, write: DraftWrite, response: Response) -> dict[str, Any]:
    if write.definition.id != workflow_id:
        raise HTTPException(status_code=422, detail="Workflow ID does not match the route.")
    if write.expected_revision is None:
        raise HTTPException(status_code=428, detail="expected_revision is required to update a draft.")
    try:
        revision = configured_workflow_registry().save_draft(
            write.definition, expected_revision=write.expected_revision
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["ETag"] = f'"{revision}"'
    return {"definition": write.definition, "revision": revision, "saved": True}


@router.post("/{workflow_id}/validate")
def validate_workflow_draft(workflow_id: str, definition: WorkflowDefinition) -> dict[str, Any]:
    if definition.id != workflow_id:
        raise HTTPException(status_code=422, detail="Workflow ID does not match the route.")
    issues = validate_workflow(definition)
    return {
        "valid": not issues,
        "issues": [asdict(issue) for issue in issues],
    }


@router.post("/{workflow_id}/publish")
def publish_workflow_draft(workflow_id: str, write: DraftWrite) -> dict[str, Any]:
    if write.definition.id != workflow_id:
        raise HTTPException(status_code=422, detail="Workflow ID does not match the route.")
    if write.expected_revision is None:
        raise HTTPException(status_code=428, detail="expected_revision is required to publish.")
    try:
        frozen = configured_workflow_registry().publish_draft(
            write.definition, expected_revision=write.expected_revision
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "definition": frozen.definition(),
        "content_hash": frozen.content_hash,
        "published_at": frozen.published_at,
        "execution": "requires_runtime_compile",
    }


@router.post("/{workflow_id}/fork")
def fork_published_workflow(workflow_id: str) -> dict[str, Any]:
    try:
        definition, revision, created = configured_workflow_registry().fork_latest(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow has no published version.") from exc
    if not created:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "draft_already_exists",
                "message": "A draft already exists for this workflow.",
                "revision": revision,
            },
        )
    return {"definition": definition, "revision": revision, "saved": True, "created": True}
