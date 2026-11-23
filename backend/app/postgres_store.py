"""Postgres persistence for the customer-runner workflow runtime.

The driver is optional and imported only when this backend is selected. This
store contains run/ticket data and therefore belongs in the customer runner's
database, not in a hosted control-plane database.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .schemas import CaseRecord, WorkflowResult
from .store import (
    _SAFE_RUN_ID,
    LocalStore,
    RunAuditEvent,
    RunEventInput,
)


class PostgresStore:
    """Postgres-backed implementation of the workflow state-store contract.

    One connection is opened per operation. Row locks and ``SKIP LOCKED`` make
    run claims and outbox leases safe across independent runner processes. A
    production deployment should put this database in the customer-controlled
    runner boundary and provide managed connection pooling at the service edge.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("A Postgres connection URL is required.")
        self.dsn = dsn.strip()
        self._initialize_schema()

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "Postgres storage requires the optional 'postgres' extra."
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    @staticmethod
    def _jsonb(value: Any):
        from psycopg.types.json import Jsonb

        return Jsonb(value)

    def _initialize_schema(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS support_schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""",
            """CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY,
                payload JSONB NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                payload JSONB NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS published_run_claims (
                idempotency_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                case_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                workflow_version INTEGER NOT NULL CHECK(workflow_version > 0),
                workflow_content_hash TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed')),
                lease_owner TEXT,
                lease_until TIMESTAMPTZ,
                attempt INTEGER NOT NULL CHECK(attempt > 0),
                updated_at TIMESTAMPTZ NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS workflow_execution_steps (
                run_id TEXT NOT NULL,
                attempt INTEGER NOT NULL CHECK(attempt > 0),
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                stage_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('complete', 'review', 'warning', 'pending', 'failed')),
                elapsed_ms INTEGER NOT NULL CHECK(elapsed_ms >= 0),
                recorded_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(run_id, attempt, ordinal)
            )""",
            """CREATE TABLE IF NOT EXISTS run_events (
                sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                from_status TEXT,
                to_status TEXT,
                decision_version INTEGER,
                score DOUBLE PRECISION,
                outcome TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS run_event_outbox (
                event_id TEXT PRIMARY KEY REFERENCES run_events(event_id),
                state TEXT NOT NULL CHECK(state IN ('pending', 'leased', 'delivered')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                available_at TIMESTAMPTZ NOT NULL,
                lease_owner TEXT,
                lease_token TEXT,
                lease_until TIMESTAMPTZ,
                delivered_at TIMESTAMPTZ,
                last_error_code TEXT
            )""",
            """CREATE INDEX IF NOT EXISTS run_event_outbox_pending
                ON run_event_outbox(state, available_at, lease_until)""",
            """CREATE INDEX IF NOT EXISTS run_events_by_run_sequence
                ON run_events(run_id, sequence)""",
            """CREATE TABLE IF NOT EXISTS monitor_state (
                id TEXT PRIMARY KEY,
                payload JSONB NOT NULL
            )""",
        )
        with self._connect() as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO support_schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING"
            )

    @staticmethod
    def _iso(value: datetime | str) -> str:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat()
        return value

    @staticmethod
    def _validate_event_input(
        case_id: str, event_type: str, metadata: dict[str, object] | None
    ) -> RunEventInput:
        return LocalStore._validate_event_input(case_id, event_type, metadata)

    def check_ready(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    def load_cases(self) -> dict[str, CaseRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, payload FROM cases ORDER BY id").fetchall()
        return {row["id"]: CaseRecord.model_validate(row["payload"]) for row in rows}

    def load_runs(self) -> dict[str, WorkflowResult]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, payload FROM runs ORDER BY id").fetchall()
        return {row["id"]: WorkflowResult.model_validate(row["payload"]) for row in rows}

    def save_case(self, case: CaseRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO cases(id, payload) VALUES (%s, %s) "
                "ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload",
                (case.id, self._jsonb(case.model_dump(mode="json"))),
            )

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
        for value, label in (
            (idempotency_key, "idempotency_key"),
            (proposed_run_id, "run_id"),
            (case_id, "case_id"),
            (workflow_id, "workflow_id"),
            (lease_owner, "lease_owner"),
        ):
            if not isinstance(value, str) or not _SAFE_RUN_ID.fullmatch(value):
                raise ValueError(f"{label} must be a short stable identifier.")
        if (
            workflow_version < 1
            or not re.fullmatch(r"[a-f0-9]{64}", workflow_content_hash)
            or not re.fullmatch(r"[a-f0-9]{64}", request_fingerprint)
        ):
            raise ValueError("Published workflow identity is invalid.")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")

        now_utc = now.astimezone(UTC)
        lease_until = now_utc + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            inserted = connection.execute(
                """INSERT INTO published_run_claims(
                    idempotency_key, run_id, case_id, workflow_id, workflow_version,
                    workflow_content_hash, request_fingerprint, state, lease_owner,
                    lease_until, attempt, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, 1, %s)
                ON CONFLICT(idempotency_key) DO NOTHING
                RETURNING run_id""",
                (
                    idempotency_key,
                    proposed_run_id,
                    case_id,
                    workflow_id,
                    workflow_version,
                    workflow_content_hash,
                    request_fingerprint,
                    lease_owner,
                    lease_until,
                    now_utc,
                ),
            ).fetchone()
            if inserted is not None:
                return {
                    "state": "acquired",
                    "run_id": inserted["run_id"],
                    "attempt": 1,
                    "lease_owner": lease_owner,
                }

            row = connection.execute(
                "SELECT * FROM published_run_claims WHERE idempotency_key=%s FOR UPDATE",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Idempotency claim disappeared during acquisition.")
            stored = (
                row["case_id"],
                row["workflow_id"],
                row["workflow_version"],
                row["workflow_content_hash"],
            )
            requested = (case_id, workflow_id, workflow_version, workflow_content_hash)
            if stored != requested or row["request_fingerprint"] != request_fingerprint:
                raise ValueError(
                    "Idempotency key was already used for a different case, workflow, or request options."
                )
            if row["state"] == "completed":
                return {
                    "state": "completed",
                    "run_id": row["run_id"],
                    "attempt": row["attempt"],
                }
            if row["state"] == "running" and row["lease_until"] > now_utc:
                return {
                    "state": "in_progress",
                    "run_id": row["run_id"],
                    "attempt": row["attempt"],
                }
            attempt = int(row["attempt"]) + 1
            connection.execute(
                """UPDATE published_run_claims
                   SET state='running', lease_owner=%s, lease_until=%s,
                       attempt=%s, updated_at=%s
                   WHERE idempotency_key=%s""",
                (lease_owner, lease_until, attempt, now_utc, idempotency_key),
            )
            return {
                "state": "acquired",
                "run_id": row["run_id"],
                "attempt": attempt,
                "lease_owner": lease_owner,
            }

    def fail_published_run_claim(
        self, idempotency_key: str, lease_owner: str, now: datetime
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE published_run_claims
                   SET state='failed', lease_owner=NULL, lease_until=NULL, updated_at=%s
                   WHERE idempotency_key=%s AND lease_owner=%s AND state='running'""",
                (now.astimezone(UTC), idempotency_key, lease_owner),
            )

    def renew_published_run_claim(
        self,
        idempotency_key: str,
        lease_owner: str,
        now: datetime,
        *,
        lease_seconds: int = 3600,
    ) -> bool:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")
        now_utc = now.astimezone(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE published_run_claims
                   SET lease_until=%s, updated_at=%s
                   WHERE idempotency_key=%s AND lease_owner=%s AND state='running'
                     AND lease_until>%s""",
                (
                    now_utc + timedelta(seconds=lease_seconds),
                    now_utc,
                    idempotency_key,
                    lease_owner,
                    now_utc,
                ),
            )
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
        if not _SAFE_RUN_ID.fullmatch(run_id) or not _SAFE_RUN_ID.fullmatch(stage_id):
            raise ValueError("run_id and stage_id must be short stable identifiers.")
        if attempt < 1 or ordinal < 0 or elapsed_ms < 0:
            raise ValueError("Execution step counters cannot be negative.")
        if status not in {"complete", "review", "warning", "pending", "failed"}:
            raise ValueError("Execution step status is unsupported.")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO workflow_execution_steps(
                    run_id, attempt, ordinal, stage_id, status, elapsed_ms, recorded_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    run_id,
                    attempt,
                    ordinal,
                    stage_id,
                    status,
                    elapsed_ms,
                    (recorded_at or datetime.now(UTC)).astimezone(UTC),
                ),
            )

    def list_execution_steps(self, run_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id, attempt, ordinal, stage_id, status, elapsed_ms, recorded_at
                   FROM workflow_execution_steps WHERE run_id=%s ORDER BY attempt, ordinal""",
                (run_id,),
            ).fetchall()
        return [
            {**row, "recorded_at": self._iso(row["recorded_at"])} for row in rows
        ]

    def save_run(
        self,
        run: WorkflowResult,
        *,
        audit_event: tuple[str, str, str, dict[str, object]] | None = None,
        execution_claim: tuple[str, str] | None = None,
    ) -> None:
        event_input: RunEventInput | None = None
        if audit_event is not None:
            event_run_id, case_id, event_type, metadata = audit_event
            if not isinstance(event_run_id, str) or not _SAFE_RUN_ID.fullmatch(event_run_id):
                raise ValueError("run_id must be a short stable identifier.")
            if event_run_id != run.id:
                raise ValueError("Audit event run_id must match the saved run.")
            event_input = self._validate_event_input(case_id, event_type, metadata)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs(id, payload) VALUES (%s, %s) "
                "ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload",
                (run.id, self._jsonb(run.model_dump(mode="json"))),
            )
            if audit_event is not None and event_input is not None:
                self._insert_run_event(
                    connection,
                    run_id=run.id,
                    case_id=audit_event[1],
                    event_type=audit_event[2],
                    metadata=event_input,
                )
            if execution_claim is not None:
                cursor = connection.execute(
                    """UPDATE published_run_claims
                       SET state='completed', lease_owner=NULL, lease_until=NULL
                       WHERE idempotency_key=%s AND lease_owner=%s AND run_id=%s
                         AND state='running'""",
                    (execution_claim[0], execution_claim[1], run.id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Published run lease was lost before completion.")

    def _insert_run_event(
        self,
        connection,
        *,
        run_id: str,
        case_id: str,
        event_type: str,
        metadata: RunEventInput,
    ) -> RunAuditEvent:
        event_id = uuid4().hex
        occurred_at = datetime.now(UTC)
        row = connection.execute(
            """INSERT INTO run_events(
                event_id, run_id, case_id, event_type, occurred_at,
                from_status, to_status, decision_version, score, outcome
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING sequence""",
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
        ).fetchone()
        connection.execute(
            """INSERT INTO run_event_outbox(event_id, state, attempts, available_at)
               VALUES (%s, 'pending', 0, %s)""",
            (event_id, occurred_at),
        )
        return RunAuditEvent(
            sequence=row["sequence"],
            event_id=event_id,
            run_id=run_id,
            case_id=case_id,
            event_type=event_type,
            occurred_at=self._iso(occurred_at),
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
        event_metadata = self._validate_event_input(case_id, event_type, metadata)
        with self._connect() as connection:
            event = self._insert_run_event(
                connection,
                run_id=run_id,
                case_id=case_id,
                event_type=event_type,
                metadata=event_metadata,
            )
        return event.model_dump(mode="json", exclude_none=True)

    def list_run_events(self, run_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT sequence, event_id, run_id, case_id, event_type,
                          occurred_at, from_status, to_status, decision_version,
                          score, outcome
                   FROM run_events WHERE run_id=%s ORDER BY sequence""",
                (run_id,),
            ).fetchall()
        return [
            RunAuditEvent.model_validate(
                {**row, "occurred_at": self._iso(row["occurred_at"])}
            ).model_dump(mode="json", exclude_none=True)
            for row in rows
        ]

    def claim_run_event_outbox(
        self,
        *,
        lease_owner: str,
        now: datetime,
        limit: int = 50,
        lease_seconds: int = 60,
    ) -> list[dict[str, object]]:
        if not isinstance(lease_owner, str) or not _SAFE_RUN_ID.fullmatch(lease_owner):
            raise ValueError("lease_owner must be a short stable identifier.")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600.")
        now_utc = now.astimezone(UTC)
        lease_until = now_utc + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT event_id FROM run_event_outbox
                   WHERE available_at <= %s
                     AND (state='pending' OR (state='leased' AND lease_until <= %s))
                   ORDER BY available_at, event_id
                   LIMIT %s FOR UPDATE SKIP LOCKED""",
                (now_utc, now_utc, limit),
            ).fetchall()
            if not rows:
                return []
            lease_tokens = {row["event_id"]: uuid4().hex for row in rows}
            for event_id, lease_token in lease_tokens.items():
                connection.execute(
                    """UPDATE run_event_outbox
                       SET state='leased', lease_owner=%s, lease_token=%s,
                           lease_until=%s, attempts=attempts+1
                       WHERE event_id=%s""",
                    (lease_owner, lease_token, lease_until, event_id),
                )
            event_ids = list(lease_tokens)
            event_rows = connection.execute(
                """SELECT e.sequence, e.event_id, e.run_id, e.case_id, e.event_type,
                          e.occurred_at, e.from_status, e.to_status, e.decision_version,
                          e.score, e.outcome, o.attempts
                   FROM run_events e JOIN run_event_outbox o USING(event_id)
                   WHERE e.event_id = ANY(%s) ORDER BY e.sequence""",
                (event_ids,),
            ).fetchall()
            result = []
            for row in event_rows:
                event_payload = {
                    key: self._iso(row[key]) if key == "occurred_at" else row[key]
                    for key in RunAuditEvent.model_fields
                }
                value = RunAuditEvent.model_validate(event_payload).model_dump(
                    mode="json", exclude_none=True
                )
                value["attempts"] = row["attempts"]
                value["lease_token"] = lease_tokens[row["event_id"]]
                result.append(value)
            return result

    def acknowledge_run_event_outbox(
        self, event_id: str, lease_token: str, now: datetime
    ) -> bool:
        if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
            raise ValueError("event_id is invalid.")
        if not isinstance(lease_token, str) or not re.fullmatch(r"[a-f0-9]{32}", lease_token):
            raise ValueError("lease_token is invalid.")
        now_utc = now.astimezone(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE run_event_outbox
                   SET state='delivered', delivered_at=%s, lease_owner=NULL,
                       lease_token=NULL, lease_until=NULL, last_error_code=NULL
                   WHERE event_id=%s AND state='leased' AND lease_token=%s
                     AND lease_until>%s""",
                (now_utc, event_id, lease_token, now_utc),
            )
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
        if not isinstance(event_id, str) or not re.fullmatch(r"[a-f0-9]{32}", event_id):
            raise ValueError("event_id is invalid.")
        if not isinstance(lease_token, str) or not re.fullmatch(r"[a-f0-9]{32}", lease_token):
            raise ValueError("lease_token is invalid.")
        if error_code is not None and not re.fullmatch(r"[a-z0-9_.-]{1,64}", error_code):
            raise ValueError("error_code must be a short non-sensitive code.")
        now_utc = now.astimezone(UTC)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE run_event_outbox
                   SET state='pending', available_at=%s, lease_owner=NULL,
                       lease_token=NULL, lease_until=NULL, last_error_code=%s
                   WHERE event_id=%s AND state='leased' AND lease_token=%s
                     AND lease_until>%s""",
                (available_at.astimezone(UTC), error_code, event_id, lease_token, now_utc),
            )
            return cursor.rowcount == 1

    def run_event_outbox_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM run_event_outbox GROUP BY state"
            ).fetchall()
        counts = {"pending": 0, "leased": 0, "delivered": 0}
        counts.update({row["state"]: int(row["count"]) for row in rows})
        return counts

    def load_monitor_state(self) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM monitor_state WHERE id='jira'"
            ).fetchone()
        if row is None:
            return {}
        value = row["payload"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return {}
        return value if isinstance(value, dict) else {}

    def save_monitor_state(self, state: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO monitor_state(id, payload) VALUES ('jira', %s)
                   ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload""",
                (self._jsonb(state),),
            )
