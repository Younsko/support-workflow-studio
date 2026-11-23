"""Local durable state for the single-process support workflow prototype."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
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


class RunEventInput(BaseModel):
    """Strict, metadata-only input accepted by the local run audit log."""

    model_config = ConfigDict(extra="forbid", strict=True)

    from_status: RunStatus | None = None
    to_status: RunStatus | None = None
    decision_version: int | None = Field(default=None, ge=0)
    score: float | None = Field(default=None, ge=0, le=10)
    outcome: PublishOutcome | None = None


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


_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class LocalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
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
                    outcome TEXT
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS run_events_by_run_sequence "
                "ON run_events(run_id, sequence)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS monitor_state (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def load_cases(self) -> dict[str, CaseRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, payload FROM cases ORDER BY rowid").fetchall()
        return {row[0]: CaseRecord.model_validate_json(row[1]) for row in rows}

    def load_runs(self) -> dict[str, WorkflowResult]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, payload FROM runs ORDER BY rowid").fetchall()
        return {row[0]: WorkflowResult.model_validate_json(row[1]) for row in rows}

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
                from_status, to_status, decision_version, score, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        sequence = connection.execute(
            "SELECT sequence FROM run_events WHERE event_id = ?", (event_id,)
        ).fetchone()[0]
        return RunAuditEvent(
            sequence=sequence,
            event_id=event_id,
            run_id=run_id,
            case_id=case_id,
            event_type=event_type,
            occurred_at=occurred_at,
            **metadata.model_dump(),
        )

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
                          decision_version, score, outcome
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


def configured_store() -> LocalStore:
    return LocalStore(configured_store_path())
