from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app import main
from app.fixtures import demo_cases
from app.schemas import WorkflowResult
from app.store import LocalStore, WorkflowStateStore, configured_store
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response


def _minimal_run(run_id: str | None = None) -> WorkflowResult:
    return WorkflowResult(
        id=run_id or uuid4().hex,
        case_id="SUP-4821",
        status="awaiting_human",
        provider_mode="demo",
        model_name="Synthetic demo",
        started_at=datetime.now(UTC).isoformat(),
        elapsed_ms=0,
        score=9.1,
        rounds=1,
        candidate_count=1,
        category="Connectivity",
        urgency="normal",
        confidence=1.0,
        evidence=[],
        plan=[],
        draft="Synthetic draft content that must not enter the audit log.",
        reviewer_findings=[],
        stages=[],
    )


def test_private_local_mode_rejects_postgres_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIVATE_LOCAL_MODE", "true")
    monkeypatch.setenv(
        "SUPPORT_WORKFLOW_DATABASE_URL",
        "postgresql://user:placeholder@db.example/database",
    )

    with pytest.raises(RuntimeError, match="PRIVATE_LOCAL_MODE refuses Postgres storage"):
        configured_store()


def test_configured_environment_file_uses_persistent_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    target = tmp_path / "runtime" / "settings.env"
    monkeypatch.setenv("SUPPORT_WORKFLOW_ENV_PATH", str(target))

    assert main._configured_env_path() == target


async def test_local_client_guard_accepts_only_configured_compose_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUPPORT_TRUSTED_LOCAL_CLIENT_IP", "172.29.241.1")

    def request_from(address: str, host: str = "127.0.0.1:8000") -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/health/live",
                "raw_path": b"/health/live",
                "query_string": b"",
                "headers": [(b"host", host.encode("ascii"))],
                "client": (address, 43100),
                "server": ("127.0.0.1", 8000),
            }
        )

    async def endpoint(_: Request) -> Response:
        return Response(status_code=204)

    trusted = await main.require_local_client(request_from("172.29.241.1"), endpoint)
    other_container = await main.require_local_client(
        request_from("172.29.241.2"), endpoint
    )
    trusted_with_remote_host = await main.require_local_client(
        request_from("172.29.241.1", host="support.example.com"), endpoint
    )

    assert trusted.status_code == 204
    assert other_container.status_code == 403
    assert trusted_with_remote_host.status_code == 403


def test_published_run_claim_is_idempotent_and_completed_with_saved_run(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    now = datetime.now(UTC)
    binding = {
        "idempotency_key": "request-1",
        "case_id": "JSM-123",
        "workflow_id": "support-intake",
        "workflow_version": 3,
        "workflow_content_hash": "a" * 64,
        "request_fingerprint": "c" * 64,
    }
    first = store.claim_published_run(
        **binding,
        proposed_run_id="RUN-A1B2C3D4",
        lease_owner="worker-a",
        now=now,
        lease_seconds=30,
    )
    assert first["state"] == "acquired"
    assert store.claim_published_run(
        **binding,
        proposed_run_id="RUN-IGNORED1",
        lease_owner="worker-b",
        now=now + timedelta(seconds=5),
        lease_seconds=30,
    )["state"] == "in_progress"
    with pytest.raises(ValueError, match="different case, workflow, or request options"):
        store.claim_published_run(
            **{**binding, "request_fingerprint": "d" * 64},
            proposed_run_id="RUN-IGNORED4",
            lease_owner="worker-b",
            now=now + timedelta(seconds=5),
        )

    run = _minimal_run("RUN-A1B2C3D4")
    run.case_id = "JSM-123"
    run.workflow_id = "support-intake"
    run.workflow_version = 3
    run.workflow_content_hash = "a" * 64
    store.save_run(run, execution_claim=("request-1", "worker-a"))

    cached = store.claim_published_run(
        **binding,
        proposed_run_id="RUN-IGNORED2",
        lease_owner="worker-c",
        now=now + timedelta(seconds=10),
    )
    assert cached["state"] == "completed"
    assert cached["run_id"] == run.id
    assert store.load_runs()[run.id].workflow_content_hash == "a" * 64


def test_expired_published_run_lease_can_be_reclaimed_with_fencing(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    now = datetime.now(UTC)
    binding = {
        "idempotency_key": "request-2",
        "case_id": "JSM-124",
        "workflow_id": "support-intake",
        "workflow_version": 1,
        "workflow_content_hash": "b" * 64,
        "request_fingerprint": "e" * 64,
    }
    original = store.claim_published_run(
        **binding,
        proposed_run_id="RUN-01020304",
        lease_owner="worker-old",
        now=now,
        lease_seconds=5,
    )
    reclaimed = store.claim_published_run(
        **binding,
        proposed_run_id="RUN-IGNORED3",
        lease_owner="worker-new",
        now=now + timedelta(seconds=6),
        lease_seconds=30,
    )
    assert original["run_id"] == reclaimed["run_id"]
    assert reclaimed["attempt"] == 2
    assert store.renew_published_run_claim(
        "request-2", "worker-new", now + timedelta(seconds=7)
    ) is True
    with pytest.raises(ValueError, match="lease was lost"):
        store.save_run(_minimal_run("RUN-01020304"), execution_claim=("request-2", "worker-old"))
    store.fail_published_run_claim("request-2", "worker-new", now + timedelta(seconds=8))


def test_old_published_claim_schema_migrates_request_fingerprint(tmp_path) -> None:
    database = tmp_path / "legacy-state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE published_run_claims (
                idempotency_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                case_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                workflow_version INTEGER NOT NULL,
                workflow_content_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed')),
                lease_owner TEXT,
                lease_until TEXT,
                attempt INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO published_run_claims (
                idempotency_key, run_id, case_id, workflow_id, workflow_version,
                workflow_content_hash, state, attempt, updated_at
            ) VALUES ('legacy-request', 'RUN-LEGACY1', 'SUP-4821', 'support-intake', 1, ?, 'failed', 1, ?)""",
            ("a" * 64, datetime.now(UTC).isoformat()),
        )

    LocalStore(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(published_run_claims)")}
        migrated = connection.execute(
            "SELECT request_fingerprint FROM published_run_claims WHERE idempotency_key='legacy-request'"
        ).fetchone()
    assert "request_fingerprint" in columns
    assert migrated == ("",)


def test_execution_step_checkpoints_persist_without_ticket_content(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    timestamp = datetime.now(UTC)

    store.record_execution_step(
        run_id="RUN-STEP1234",
        attempt=2,
        ordinal=0,
        stage_id="intake",
        status="complete",
        elapsed_ms=125,
        recorded_at=timestamp,
    )
    store.record_execution_step(
        run_id="RUN-STEP1234",
        attempt=2,
        ordinal=1,
        stage_id="knowledge",
        status="complete",
        elapsed_ms=40,
        recorded_at=timestamp,
    )

    steps = store.list_execution_steps("RUN-STEP1234")
    assert [(step["attempt"], step["ordinal"], step["stage_id"]) for step in steps] == [
        (2, 0, "intake"),
        (2, 1, "knowledge"),
    ]
    assert all("payload" not in step and "detail" not in step for step in steps)


def test_local_state_survives_reload_and_keeps_human_decision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    store = LocalStore(tmp_path / "state.sqlite3")
    monkeypatch.setattr(main, "STATE_STORE", store)
    main.CASES.clear()
    main.CASES.update({case.id: case for case in demo_cases()})
    main.RUNS.clear()
    client = TestClient(main.app)

    ticket = client.post(
        "/api/cases",
        json={
            "title": "VPN disconnects after sign-in",
            "requester": "Synthetic Requester",
            "body": "My VPN disconnects during work. I can still access the web. Please help.",
            "impact": "One person affected",
        },
    )
    assert ticket.status_code == 201
    run_response = client.post(
        "/api/workflows", json={"case_id": "SUP-4825", "candidate_count": 1}
    )
    assert run_response.status_code == 201
    run = run_response.json()
    decision = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"]},
    )
    assert decision.status_code == 200

    main.CASES.clear()
    main.RUNS.clear()
    main.CASES.update(store.load_cases())
    main.RUNS.update(store.load_runs())

    assert ticket.json()["id"] in main.CASES
    restored = client.get(f"/api/workflows/latest/{run['case_id']}")
    assert restored.status_code == 200
    assert restored.json()["id"] == run["id"]
    assert restored.json()["status"] == "approved"
    assert restored.json()["ticket_comment_published"] is False
    events_response = client.get(f"/api/workflows/{run['id']}/events")
    assert events_response.status_code == 200
    events = events_response.json()
    assert [event["event_type"] for event in events] == [
        "workflow.started",
        "decision.approved",
    ]
    assert events[0]["to_status"] == "awaiting_human"
    assert events[1]["to_status"] == "approved"
    assert "body" not in str(events).lower()
    assert "draft" not in str(events).lower()
    monkeypatch.setattr(main, "STATE_STORE", None)


def test_jira_monitor_checkpoint_survives_restart(tmp_path, monkeypatch) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    checkpoint = {
        "seen_keys": ["SUP-100", "SUP-101"],
        "pending_attempts": {"SUP-101": 2},
        "local_tags": {"SUP-101": ["studio-triage-retry-pending"]},
        "monitor_baselined": True,
        "monitor_baseline_at": "2026-11-22T10:00:00+00:00",
        "auto_triage_baseline_at": "2026-11-22T10:05:00+00:00",
        "auto_triage_was_enabled": True,
    }
    store.save_monitor_state(checkpoint)

    monkeypatch.setattr(main, "JIRA_SEEN_KEYS", set())
    monkeypatch.setattr(main, "JIRA_PENDING_TRIAGE_ATTEMPTS", {})
    monkeypatch.setattr(main, "JIRA_LOCAL_TAGS", {})
    monkeypatch.setattr(main, "JIRA_MONITOR_BASELINED", False)
    monkeypatch.setattr(main, "JIRA_MONITOR_BASELINE_AT", None)
    monkeypatch.setattr(main, "JIRA_AUTO_TRIAGE_BASELINE_AT", None)
    monkeypatch.setattr(main, "JIRA_AUTO_TRIAGE_WAS_ENABLED", False)

    main._restore_monitor_checkpoint(store.load_monitor_state())

    assert main.JIRA_SEEN_KEYS == {"SUP-100", "SUP-101"}
    assert main.JIRA_PENDING_TRIAGE_ATTEMPTS == {"SUP-101": 2}
    assert main.JIRA_LOCAL_TAGS["SUP-101"] == {"studio-triage-retry-pending"}
    assert main.JIRA_MONITOR_BASELINED is True
    assert main.JIRA_MONITOR_BASELINE_AT.isoformat() == checkpoint["monitor_baseline_at"]
    assert main.JIRA_AUTO_TRIAGE_BASELINE_AT.isoformat() == checkpoint["auto_triage_baseline_at"]
    assert main.JIRA_AUTO_TRIAGE_WAS_ENABLED is True


def test_bad_jira_monitor_checkpoint_defaults_to_empty_state(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO monitor_state(id, payload) VALUES ('jira', '[]')"
        )
        connection.commit()
    assert store.load_monitor_state() == {}


def test_run_audit_events_survive_reopen_in_stable_order_without_content(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = LocalStore(database)
    run_id = uuid4().hex
    first = store.append_run_event(
        run_id,
        "SUP-4821",
        "workflow.started",
        {"to_status": "awaiting_human", "score": 9.1},
    )
    second = store.append_run_event(
        run_id,
        "SUP-4821",
        "decision.approved",
        {"from_status": "awaiting_human", "to_status": "approved", "decision_version": 0},
    )

    reopened = LocalStore(database)
    events = reopened.list_run_events(run_id)

    assert [event["event_id"] for event in events] == [first["event_id"], second["event_id"]]
    assert [event["sequence"] for event in events] == sorted(
        [event["sequence"] for event in events]
    )
    assert events[0]["sequence"] < events[1]["sequence"]
    assert all(datetime.fromisoformat(event["occurred_at"]).tzinfo for event in events)
    assert events[1]["to_status"] == "approved"
    assert "draft" not in events[0]
    with reopened._connect() as connection:
        persisted = connection.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
    serialized_rows = repr([tuple(row) for row in persisted])
    assert "Synthetic draft content" not in serialized_rows
    assert "api_key" not in serialized_rows.lower()


@pytest.mark.parametrize(
    "metadata",
    [
        {"draft": "private ticket reply"},
        {"body": "private ticket body"},
        {"api_key": "secret"},
        {"token": "secret"},
        {"score": "high"},
    ],
)
def test_run_audit_rejects_content_and_secret_shaped_metadata(tmp_path, metadata) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")

    with pytest.raises(ValueError):
        store.append_run_event(uuid4().hex, "SUP-4821", "workflow.started", metadata)


def test_run_audit_rejects_unknown_event_type_and_unsafe_identifiers(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError):
        store.append_run_event(uuid4().hex, "SUP-4821", "custom.secret", {})
    with pytest.raises(ValueError):
        store.append_run_event("secret run identifier", "SUP-4821", "workflow.started", {})
    with pytest.raises(ValueError):
        store.append_run_event(uuid4().hex, "customer body with spaces", "workflow.started", {})


def test_save_run_can_atomically_append_a_metadata_event(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    run = _minimal_run()

    store.save_run(
        run,
        audit_event=(
            run.id,
            run.case_id,
            "workflow.started",
            {"to_status": "awaiting_human", "score": run.score},
        ),
    )

    assert store.load_runs()[run.id].id == run.id
    assert store.list_run_events(run.id)[0]["event_type"] == "workflow.started"


def test_save_run_rolls_back_run_when_optional_event_insert_fails(tmp_path, monkeypatch) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    run = _minimal_run()

    def fail_insert(*_args, **_kwargs):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(store, "_insert_run_event", fail_insert)
    with pytest.raises(RuntimeError, match="simulated audit write failure"):
        store.save_run(
            run,
            audit_event=(run.id, run.case_id, "workflow.started", {}),
        )

    assert run.id not in store.load_runs()
    assert store.list_run_events(run.id) == []
    assert store.run_event_outbox_counts() == {"pending": 0, "leased": 0, "delivered": 0}


def test_run_event_outbox_is_transactional_and_owner_fenced(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    run = _minimal_run()
    store.save_run(
        run,
        audit_event=(run.id, run.case_id, "workflow.started", {"to_status": "awaiting_human"}),
    )
    now = datetime.now(UTC) + timedelta(seconds=1)

    events = store.claim_run_event_outbox(lease_owner="dispatcher-a", now=now)
    assert len(events) == 1
    event = events[0]
    event_id = event["event_id"]
    assert event["event_type"] == "workflow.started"
    assert event["attempts"] == 1
    assert "draft" not in event
    assert store.run_event_outbox_counts() == {"pending": 0, "leased": 1, "delivered": 0}
    assert not store.acknowledge_run_event_outbox(event_id, "0" * 32, now + timedelta(seconds=1))
    assert store.acknowledge_run_event_outbox(
        event_id, event["lease_token"], now + timedelta(seconds=1)
    )
    assert not store.acknowledge_run_event_outbox(
        event_id, event["lease_token"], now + timedelta(seconds=2)
    )
    assert store.run_event_outbox_counts() == {"pending": 0, "leased": 0, "delivered": 1}


def test_run_event_outbox_reclaims_expired_lease_and_releases_for_retry(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    run = _minimal_run()
    event = store.append_run_event(run.id, run.case_id, "decision.approved", {"to_status": "approved"})
    event_id = event["event_id"]
    now = datetime.now(UTC) + timedelta(seconds=1)

    first = store.claim_run_event_outbox(
        lease_owner="worker-old", now=now, lease_seconds=1
    )[0]
    assert first["attempts"] == 1
    reclaimed = store.claim_run_event_outbox(
        lease_owner="worker-old", now=now + timedelta(seconds=2)
    )[0]
    assert reclaimed["attempts"] == 2
    assert first["lease_token"] != reclaimed["lease_token"]
    assert not store.acknowledge_run_event_outbox(
        event_id, first["lease_token"], now + timedelta(seconds=2)
    )
    retry_at = now + timedelta(seconds=8)
    assert store.release_run_event_outbox(
        event_id,
        reclaimed["lease_token"],
        now=now + timedelta(seconds=3),
        available_at=retry_at,
        error_code="transport_unavailable",
    )
    assert store.claim_run_event_outbox(
        lease_owner="worker-next", now=now + timedelta(seconds=7)
    ) == []
    retried = store.claim_run_event_outbox(
        lease_owner="worker-next", now=retry_at
    )[0]
    assert retried["attempts"] == 3


def test_run_event_outbox_rejects_sensitive_error_codes_and_expired_release(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")
    event = store.append_run_event(uuid4().hex, "SUP-4821", "workflow.started", {})
    event_id = event["event_id"]
    now = datetime.now(UTC) + timedelta(seconds=1)
    first_claim = store.claim_run_event_outbox(
        lease_owner="worker-a", now=now, lease_seconds=1
    )[0]

    with pytest.raises(ValueError, match="non-sensitive"):
        store.release_run_event_outbox(
            event_id, first_claim["lease_token"], now=now,
            available_at=now + timedelta(seconds=2), error_code="ticket body: secret",
        )
    assert not store.release_run_event_outbox(
        event_id,
        first_claim["lease_token"],
        now=now + timedelta(seconds=2),
        available_at=now + timedelta(seconds=3),
        error_code="timeout",
    )


def test_local_store_readiness_probe_checks_database_connection(tmp_path, monkeypatch) -> None:
    import sqlite3

    store = LocalStore(tmp_path / "ready.sqlite3")
    store.check_ready()

    def unavailable():
        raise sqlite3.OperationalError("storage unavailable")

    monkeypatch.setattr(store, "_connect", unavailable)
    with pytest.raises(sqlite3.OperationalError, match="storage unavailable"):
        store.check_ready()


def test_sqlite_implementation_satisfies_workflow_state_store_contract(tmp_path) -> None:
    store = LocalStore(tmp_path / "state.sqlite3")

    assert isinstance(store, WorkflowStateStore)
