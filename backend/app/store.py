"""Local durable state for the single-process support workflow prototype."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .schemas import CaseRecord, WorkflowResult

RunEventType = Literal[
    "workflow.started",
    "draft.edited",
    "decision.approved",
    "decision.rejected",
    "publish.attempted",
    "publish.succeeded",
    "publish.failed",
    "publish.unknown",
]
RunStatus = Literal["awaiting_human", "approved", "edited", "rejected", "failed"]
PublishOutcome = Literal["not_started", "published", "unknown", "failed"]
AuditActorRole = Literal["Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"]


class RunEventInput(BaseModel):
    """Strict, metadata-only input accepted by the local run audit log."""

    model_config = ConfigDict(extra="forbid", strict=True)

    from_status: RunStatus | None = None
    to_status: RunStatus | None = None
    decision_version: int | None = Field(default=None, ge=0)
    score: float | None = Field(default=None, ge=0, le=10)
    outcome: PublishOutcome | None = None
    actor_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    actor_role: AuditActorRole | None = None


class RunAuditEvent(BaseModel):
    """JSON-safe view of one append-only local run event."""

    model_config = ConfigDict(extra="forbid")

    sequence: int
    event_id: str
    run_id: str
    case_id: str
    event_type: RunEventType
    occurred_at: str
    from_status: RunStatus | None = None
    to_status: RunStatus | None = None
    decision_version: int | None = None
    score: float | None = None
    outcome: PublishOutcome | None = None
    actor_id: str | None = None
    actor_role: AuditActorRole | None = None


_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_CONTROL_PLANE_ID = re.compile(r"^[a-f0-9]{32}$")


@runtime_checkable
class WorkflowStateStore(Protocol):
    """Persistence contract consumed by the local workflow runtime.

    Implementations must make each method's documented state transition
    durable before returning. LocalStore is the default; PostgresStore is an
    opt-in customer-runner backend selected with SUPPORT_WORKFLOW_DATABASE_URL.
    """

    def check_ready(self) -> None: ...
    def bind_runner_identity(self, workspace_id: str, runner_id: str) -> None: ...
    def load_cases(self) -> dict[str, CaseRecord]: ...
    def load_runs(self) -> dict[str, WorkflowResult]: ...
    def save_case(self, case: CaseRecord) -> None: ...
    def save_run(
        self,
        run: WorkflowResult,
        *,
        audit_event: tuple[str, str, str, dict[str, object]] | None = None,
        execution_claim: tuple[str, str] | None = None,
    ) -> None: ...
    def claim_published_run(
        self,
        *,
        idempotency_key: str,
        proposed_run_id: str,
        case_id: str,
        workflow_id: str,
        workflow_version: int,
        workflow_content_hash: str,
        request_fingerprint: str,
        lease_owner: str,
        now: datetime,
        lease_seconds: int = 300,
    ) -> dict[str, object]: ...
    def fail_published_run_claim(
        self, idempotency_key: str, lease_owner: str, now: datetime
    ) -> None: ...
    def renew_published_run_claim(
        self, idempotency_key: str, lease_owner: str, now: datetime,
        *, lease_seconds: int = 3600,
    ) -> bool: ...
    def record_execution_step(
        self,
        *,
        run_id: str,
        attempt: int,
        ordinal: int,
        stage_id: str,
        status: str,
        elapsed_ms: int,
        recorded_at: datetime | None = None,
    ) -> None: ...
    def list_execution_steps(self, run_id: str) -> list[dict[str, object]]: ...
    def append_run_event(
        self, run_id: str, case_id: str, event_type: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]: ...
    def list_run_events(self, run_id: str) -> list[dict[str, object]]: ...
    def load_monitor_state(self) -> dict[str, object]: ...
    def save_monitor_state(self, state: dict[str, object]) -> None: ...
    def claim_run_event_outbox(
        self, *, lease_owner: str, now: datetime, limit: int = 50,
        lease_seconds: int = 60,
    ) -> list[dict[str, object]]: ...
    def acknowledge_run_event_outbox(
        self, event_id: str, lease_token: str, now: datetime
    ) -> bool: ...
    def release_run_event_outbox(
        self, event_id: str, lease_token: str, *, now: datetime,
        available_at: datetime, error_code: str | None = None,
    ) -> bool: ...
    def run_event_outbox_counts(self) -> dict[str, int]: ...


class LocalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runner_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    workspace_id TEXT NOT NULL,
                    runner_id TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS published_run_claims (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    case_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_version INTEGER NOT NULL,
                    workflow_content_hash TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed')),
                    lease_owner TEXT,
                    lease_until TEXT,
                    attempt INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            claim_columns = {row[1] for row in connection.execute("PRAGMA table_info(published_run_claims)")}
            if "request_fingerprint" not in claim_columns:
                connection.execute("ALTER TABLE published_run_claims ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workflow_execution_steps (
                    run_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL,
                    stage_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('complete', 'review', 'warning', 'pending', 'failed')),
                    elapsed_ms INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, attempt, ordinal)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    decision_version INTEGER,
                    score REAL,
                    outcome TEXT,
                    actor_id TEXT,
                    actor_role TEXT
                )"""
            )
            run_event_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(run_events)")
            }
            if "actor_id" not in run_event_columns:
                connection.execute("ALTER TABLE run_events ADD COLUMN actor_id TEXT")
            if "actor_role" not in run_event_columns:
                connection.execute("ALTER TABLE run_events ADD COLUMN actor_role TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS run_events_by_run_sequence "
                "ON run_events(run_id, sequence)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS run_event_outbox (
                    event_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN ('pending', 'leased', 'delivered')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_until TEXT,
                    delivered_at TEXT,
                    last_error_code TEXT
                )"""
            )
            outbox_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(run_event_outbox)")
            }
            if "lease_token" not in outbox_columns:
                connection.execute("ALTER TABLE run_event_outbox ADD COLUMN lease_token TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS run_event_outbox_pending "
                "ON run_event_outbox(state, available_at, lease_until)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS monitor_state (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def check_ready(self) -> None:
        """Confirm the local state database can accept a lightweight read."""
        with closing(self._connect()) as connection:
            connection.execute("SELECT 1").fetchone()

    def bind_runner_identity(self, workspace_id: str, runner_id: str) -> None:
        """Bind this dedicated state database to exactly one control-plane runner."""
        if not _SAFE_CONTROL_PLANE_ID.fullmatch(workspace_id) or not _SAFE_CONTROL_PLANE_ID.fullmatch(runner_id):
            raise ValueError("Workspace and runner IDs must be control-plane resource IDs.")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT workspace_id, runner_id FROM runner_identity WHERE singleton = 1"
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO runner_identity(singleton, workspace_id, runner_id) VALUES (1, ?, ?)",
                    (workspace_id, runner_id),
                )
            elif current["workspace_id"] != workspace_id or current["runner_id"] != runner_id:
                raise RuntimeError("State database is already bound to another workspace runner.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_cases(self) -> dict[str, CaseRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, payload FROM cases ORDER BY rowid").fetchall()
        return {row[0]: CaseRecord.model_validate_json(row[1]) for row in rows}

    def load_runs(self) -> dict[str, WorkflowResult]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, payload FROM runs ORDER BY rowid").fetchall()
        return {row[0]: WorkflowResult.model_validate_json(row[1]) for row in rows}

    def claim_published_run(
        self,
        *,
        idempotency_key: str,
        proposed_run_id: str,
        case_id: str,
        workflow_id: str,
        workflow_version: int,
        workflow_content_hash: str,
        request_fingerprint: str,
        lease_owner: str,
        now: datetime,
        lease_seconds: int = 300,
    ) -> dict[str, object]:
        """Reserve one published run atomically; expired workers are fenced by owner."""
        for value, label in (
            (idempotency_key, "idempotency_key"),
            (proposed_run_id, "run_id"),
            (case_id, "case_id"),
            (workflow_id, "workflow_id"),
            (lease_owner, "lease_owner"),
        ):
            if not isinstance(value, str) or not _SAFE_RUN_ID.fullmatch(value):
                raise ValueError(f"{label} must be a short stable identifier.")
        if workflow_version < 1 or not re.fullmatch(r"[a-f0-9]{64}", workflow_content_hash) or not re.fullmatch(r"[a-f0-9]{64}", request_fingerprint):
            raise ValueError("Published workflow identity is invalid.")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")
        now_utc = now.astimezone(UTC)
        now_value = now_utc.isoformat()
        lease_until = (now_utc + timedelta(seconds=lease_seconds)).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM published_run_claims WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            binding = (case_id, workflow_id, workflow_version, workflow_content_hash)
            if row is not None:
                stored = (row["case_id"], row["workflow_id"], row["workflow_version"], row["workflow_content_hash"])
                if stored != binding or row["request_fingerprint"] != request_fingerprint:
                    raise ValueError("Idempotency key was already used for a different case, workflow, or request options.")
                if row["state"] == "completed":
                    connection.commit()
                    return {"state": "completed", "run_id": row["run_id"], "attempt": row["attempt"]}
                if row["state"] == "running" and row["lease_until"] and row["lease_until"] > now_value:
                    connection.commit()
                    return {"state": "in_progress", "run_id": row["run_id"], "attempt": row["attempt"]}
                attempt = int(row["attempt"]) + 1
                connection.execute(
                    "UPDATE published_run_claims SET state='running', lease_owner=?, lease_until=?, attempt=?, updated_at=? WHERE idempotency_key=?",
                    (lease_owner, lease_until, attempt, now_value, idempotency_key),
                )
                run_id = row["run_id"]
            else:
                attempt = 1
                run_id = proposed_run_id
                connection.execute(
                    """INSERT INTO published_run_claims(
                        idempotency_key, run_id, case_id, workflow_id, workflow_version,
                        workflow_content_hash, request_fingerprint, state, lease_owner, lease_until, attempt, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)""",
                    (idempotency_key, run_id, case_id, workflow_id, workflow_version,
                     workflow_content_hash, request_fingerprint, lease_owner, lease_until, attempt, now_value),
                )
            connection.commit()
            return {"state": "acquired", "run_id": run_id, "attempt": attempt, "lease_owner": lease_owner}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_published_run_claim(self, idempotency_key: str, lease_owner: str, now: datetime) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE published_run_claims SET state='failed', lease_owner=NULL, lease_until=NULL, updated_at=? WHERE idempotency_key=? AND lease_owner=? AND state='running'",
                (now.astimezone(UTC).isoformat(), idempotency_key, lease_owner),
            )
            connection.commit()

    def renew_published_run_claim(
        self, idempotency_key: str, lease_owner: str, now: datetime, *, lease_seconds: int = 3600
    ) -> bool:
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")
        now_utc = now.astimezone(UTC)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "UPDATE published_run_claims SET lease_until=?, updated_at=? WHERE idempotency_key=? AND lease_owner=? AND state='running' AND lease_until>?",
                ((now_utc + timedelta(seconds=lease_seconds)).isoformat(), now_utc.isoformat(),
                 idempotency_key, lease_owner, now_utc.isoformat()),
            )
            connection.commit()
        return cursor.rowcount == 1

    def record_execution_step(
        self,
        *,
        run_id: str,
        attempt: int,
        ordinal: int,
        stage_id: str,
        status: str,
        elapsed_ms: int,
        recorded_at: datetime | None = None,
    ) -> None:
        """Persist content-free progress so interrupted runs have a durable checkpoint trail."""
        if not _SAFE_RUN_ID.fullmatch(run_id) or not _SAFE_RUN_ID.fullmatch(stage_id):
            raise ValueError("run_id and stage_id must be short stable identifiers.")
        if attempt < 1 or ordinal < 0 or elapsed_ms < 0:
            raise ValueError("Execution step counters cannot be negative.")
        if status not in {"complete", "review", "warning", "pending", "failed"}:
            raise ValueError("Execution step status is unsupported.")
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO workflow_execution_steps(run_id, attempt, ordinal, stage_id, status, elapsed_ms, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, attempt, ordinal, stage_id, status, elapsed_ms,
                 (recorded_at or datetime.now(UTC)).astimezone(UTC).isoformat()),
            )
            connection.commit()

    def list_execution_steps(self, run_id: str) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT run_id, attempt, ordinal, stage_id, status, elapsed_ms, recorded_at FROM workflow_execution_steps WHERE run_id=? ORDER BY attempt, ordinal",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_case(self, case: CaseRecord) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO cases(id, payload) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (case.id, case.model_dump_json()),
            )
            connection.commit()

    def save_run(
        self,
        run: WorkflowResult,
        *,
        audit_event: tuple[str, str, str, dict[str, object]] | None = None,
        execution_claim: tuple[str, str] | None = None,
    ) -> None:
        """Save a run and, optionally, its audit event in one SQLite transaction."""
        event_input: RunEventInput | None = None
        if audit_event is not None:
            event_run_id, case_id, event_type, metadata = audit_event
            if not isinstance(event_run_id, str) or not _SAFE_RUN_ID.fullmatch(event_run_id):
                raise ValueError("run_id must be a short stable identifier.")
            if event_run_id != run.id:
                raise ValueError("Audit event run_id must match the saved run.")
            event_input = self._validate_event_input(case_id, event_type, metadata)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO runs(id, payload) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (run.id, run.model_dump_json()),
            )
            if event_input is not None and audit_event is not None:
                # Both writes share the transaction so state and its event
                # cannot diverge if either write fails.
                self._insert_run_event(
                    connection,
                    run_id=run.id,
                    case_id=audit_event[1],
                    event_type=audit_event[2],
                    metadata=event_input,
                )
            if execution_claim is not None:
                cursor = connection.execute(
                    "UPDATE published_run_claims SET state='completed', lease_owner=NULL, lease_until=NULL WHERE idempotency_key=? AND lease_owner=? AND run_id=? AND state='running'",
                    (execution_claim[0], execution_claim[1], run.id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Published run lease was lost before completion.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_event_input(
        case_id: str, event_type: str, metadata: dict[str, object] | None
    ) -> RunEventInput:
        if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError("case_id must be a short stable identifier.")
        try:
            RunEventInput.model_validate(metadata or {})
            # Event type validation is deliberately separate from metadata so
            # only a fixed vocabulary can ever be persisted.
            RunAuditEvent.model_validate(
                {
                    "sequence": 1,
                    "event_id": "validation-only",
                    "run_id": "validation-only",
                    "case_id": case_id,
                    "event_type": event_type,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    **(metadata or {}),
                }
            )
        except ValidationError as exc:
            raise ValueError("Run audit event contains invalid or disallowed fields.") from exc
        return RunEventInput.model_validate(metadata or {})

    @staticmethod
    def _insert_run_event(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        case_id: str,
        event_type: str,
        metadata: RunEventInput,
    ) -> RunAuditEvent:
        event_id = uuid4().hex
        occurred_at = datetime.now(UTC).isoformat()
        connection.execute(
            """INSERT INTO run_events(
                event_id, run_id, case_id, event_type, occurred_at,
                from_status, to_status, decision_version, score, outcome, actor_id, actor_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                run_id,
                case_id,
                event_type,
                occurred_at,
                metadata.from_status,
                metadata.to_status,
                metadata.decision_version,
                metadata.score,
                metadata.outcome,
                metadata.actor_id,
                metadata.actor_role,
            ),
        )
        sequence = connection.execute(
            "SELECT sequence FROM run_events WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO run_event_outbox(event_id, state, attempts, available_at) "
            "VALUES (?, 'pending', 0, ?)",
            (event_id, occurred_at),
        )
        return RunAuditEvent(
            sequence=sequence,
            event_id=event_id,
            run_id=run_id,
            case_id=case_id,
            event_type=event_type,
            occurred_at=occurred_at,
            **metadata.model_dump(),
        )

    def claim_run_event_outbox(
        self,
        *,
        lease_owner: str,
        now: datetime,
        limit: int = 50,
        lease_seconds: int = 60,
    ) -> list[dict[str, object]]:
        """Lease metadata-only audit events for an external dispatcher.

        Claiming is atomic on this single SQLite host. This is a local outbox
        seam, not a distributed Postgres queue or a hosted delivery service.
        """
        if not isinstance(lease_owner, str) or not _SAFE_RUN_ID.fullmatch(lease_owner):
            raise ValueError("lease_owner must be a short stable identifier.")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")
        now_value = now.astimezone(UTC).isoformat()
        lease_until = (now.astimezone(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT event_id FROM run_event_outbox
                   WHERE available_at <= ?
                     AND (state = 'pending' OR (state = 'leased' AND lease_until <= ?))
                   ORDER BY available_at, event_id LIMIT ?""",
                (now_value, now_value, limit),
            ).fetchall()
            event_ids = [row[0] for row in rows]
            if not event_ids:
                connection.commit()
                return []
            placeholders = ",".join("?" for _ in event_ids)
            lease_tokens = {event_id: uuid4().hex for event_id in event_ids}
            connection.execute(
                f"""UPDATE run_event_outbox
                    SET state='leased', lease_owner=?, lease_until=?, attempts=attempts + 1
                    WHERE event_id IN ({placeholders})""",
                (lease_owner, lease_until, *event_ids),
            )
            for event_id, lease_token in lease_tokens.items():
                connection.execute(
                    "UPDATE run_event_outbox SET lease_token=? WHERE event_id=?",
                    (lease_token, event_id),
                )
            event_rows = connection.execute(
                f"""SELECT e.sequence, e.event_id, e.run_id, e.case_id, e.event_type,
                          e.occurred_at, e.from_status, e.to_status, e.decision_version,
                          e.score, e.outcome, e.actor_id, e.actor_role, o.attempts
                   FROM run_events e JOIN run_event_outbox o USING(event_id)
                   WHERE e.event_id IN ({placeholders}) ORDER BY e.sequence""",
                event_ids,
            ).fetchall()
            result: list[dict[str, object]] = []
            for row in event_rows:
                value = RunAuditEvent.model_validate(
                    {key: row[key] for key in RunAuditEvent.model_fields}
                ).model_dump(
                    mode="json", exclude_none=True
                )
                value["attempts"] = row["attempts"]
                value["lease_token"] = lease_tokens[row["event_id"]]
                result.append(value)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def acknowledge_run_event_outbox(self, event_id: str, lease_token: str, now: datetime) -> bool:
        """Mark a leased event delivered using the token for that exact claim."""
        if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
            raise ValueError("event_id is invalid.")
        if not isinstance(lease_token, str) or not re.fullmatch(r"[a-f0-9]{32}", lease_token):
            raise ValueError("lease_token is invalid.")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """UPDATE run_event_outbox
                   SET state='delivered', delivered_at=?, lease_owner=NULL,
                       lease_token=NULL, lease_until=NULL, last_error_code=NULL
                   WHERE event_id=? AND state='leased' AND lease_token=? AND lease_until>?""",
                (now.astimezone(UTC).isoformat(), event_id, lease_token,
                 now.astimezone(UTC).isoformat()),
            )
            connection.commit()
        return cursor.rowcount == 1

    def release_run_event_outbox(
        self,
        event_id: str,
        lease_token: str,
        *,
        now: datetime,
        available_at: datetime,
        error_code: str | None = None,
    ) -> bool:
        """Return an event to pending with a bounded, non-sensitive error code."""
        if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
            raise ValueError("event_id is invalid.")
        if not isinstance(lease_token, str) or not re.fullmatch(r"[a-f0-9]{32}", lease_token):
            raise ValueError("lease_token is invalid.")
        if error_code is not None and not re.fullmatch(r"[a-z0-9_.-]{1,64}", error_code):
            raise ValueError("error_code must be a short non-sensitive code.")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """UPDATE run_event_outbox
                   SET state='pending', available_at=?, lease_owner=NULL, lease_until=NULL,
                       lease_token=NULL, last_error_code=?
                   WHERE event_id=? AND state='leased' AND lease_token=? AND lease_until>?""",
                (available_at.astimezone(UTC).isoformat(), error_code, event_id, lease_token,
                 now.astimezone(UTC).isoformat()),
            )
            connection.commit()
        return cursor.rowcount == 1

    def run_event_outbox_counts(self) -> dict[str, int]:
        """Return bounded operational counts without exposing event contents."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM run_event_outbox GROUP BY state"
            ).fetchall()
        counts = {"pending": 0, "leased": 0, "delivered": 0}
        counts.update({row["state"]: int(row["count"]) for row in rows})
        return counts

    def append_run_event(
        self,
        run_id: str,
        case_id: str,
        event_type: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be a short stable identifier.")
        metadata_input = self._validate_event_input(case_id, event_type, metadata)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = self._insert_run_event(
                connection,
                run_id=run_id,
                case_id=case_id,
                event_type=event_type,
                metadata=metadata_input,
            )
            connection.commit()
        return event.model_dump(mode="json", exclude_none=True)

    def list_run_events(self, run_id: str) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT sequence, event_id, run_id, case_id, event_type,
                          occurred_at, from_status, to_status,
                          decision_version, score, outcome, actor_id, actor_role
                   FROM run_events WHERE run_id = ? ORDER BY sequence""",
                (run_id,),
            ).fetchall()
        return [
            RunAuditEvent.model_validate(dict(row)).model_dump(
                mode="json", exclude_none=True
            )
            for row in rows
        ]

    def load_monitor_state(self) -> dict[str, object]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM monitor_state WHERE id = 'jira'"
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_monitor_state(self, state: dict[str, object]) -> None:
        payload = json.dumps(state, separators=(",", ":"), sort_keys=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO monitor_state(id, payload) VALUES ('jira', ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (payload,),
            )
            connection.commit()


def configured_store_path() -> Path:
    default_name = "private-state.sqlite3" if os.getenv("PRIVATE_LOCAL_MODE", "").lower() in {"1", "true", "yes"} else "state.sqlite3"
    default_path = Path(__file__).resolve().parents[2] / ".local" / default_name
    return Path(os.getenv("SUPPORT_WORKFLOW_DB_PATH", str(default_path))).expanduser()


def configured_store() -> WorkflowStateStore:
    postgres_url = os.getenv("SUPPORT_WORKFLOW_DATABASE_URL", "").strip()
    if postgres_url:
        if os.getenv("PRIVATE_LOCAL_MODE", "").strip().lower() in {"1", "true", "yes"}:
            raise RuntimeError(
                "PRIVATE_LOCAL_MODE refuses Postgres storage because the database host cannot be verified as local."
            )
        from .postgres_store import PostgresStore

        return PostgresStore(postgres_url)
    return LocalStore(configured_store_path())
