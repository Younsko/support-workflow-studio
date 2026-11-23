from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from app.fixtures import demo_cases
from app.postgres_store import PostgresStore
from app.schemas import WorkflowResult
from app.store import configured_store


@pytest.fixture
def store() -> PostgresStore:
    dsn = os.getenv("POSTGRES_TEST_URL", "").strip()
    if not dsn:
        pytest.skip("Postgres integration tests require POSTGRES_TEST_URL.")
    pytest.importorskip("psycopg")
    value = PostgresStore(dsn)
    with value._connect() as connection:
        connection.execute(
            """TRUNCATE TABLE cases, runs, published_run_claims,
                workflow_execution_steps, run_event_outbox, run_events,
                monitor_state, runner_identity RESTART IDENTITY CASCADE"""
        )
    return value


def _run(run_id: str, case_id: str) -> WorkflowResult:
    return WorkflowResult(
        id=run_id,
        case_id=case_id,
        status="awaiting_human",
        provider_mode="demo",
        model_name="Synthetic demo",
        started_at=datetime.now(UTC).isoformat(),
        elapsed_ms=12,
        score=9.2,
        rounds=1,
        candidate_count=1,
        category="Connectivity",
        urgency="normal",
        confidence=0.9,
        evidence=[],
        plan=["Verify the reported behavior with the approved guide."],
        draft="A reviewed synthetic reply.",
        reviewer_findings=[],
        stages=[],
    )


def _claim(store: PostgresStore, *, now: datetime, owner: str = "worker-one") -> dict[str, object]:
    return store.claim_published_run(
        idempotency_key="request-001",
        proposed_run_id="RUN-00112233",
        case_id="JSM-123",
        workflow_id="support-intake",
        workflow_version=2,
        workflow_content_hash="a" * 64,
        request_fingerprint="b" * 64,
        lease_owner=owner,
        now=now,
        lease_seconds=10,
    )


def test_postgres_schema_is_repeatable_and_cases_runs_survive_reopen(store: PostgresStore) -> None:
    case = demo_cases()[0]
    run = _run("RUN-CASE1234", case.id)
    store.save_case(case)
    store.save_run(run)

    reopened = PostgresStore(store.dsn)

    assert reopened.check_ready() is None
    assert reopened.load_cases()[case.id] == case
    assert reopened.load_runs()[run.id] == run
    with reopened._connect() as connection:
        versions = connection.execute(
            "SELECT version FROM support_schema_migrations ORDER BY version"
        ).fetchall()
    assert [row["version"] for row in versions] == [1, 2, 3]


def test_postgres_store_identity_binding_rejects_cross_workspace_reuse(
    store: PostgresStore,
) -> None:
    workspace_id = "9" * 32
    runner_id = "a" * 32
    store.bind_runner_identity(workspace_id, runner_id)
    PostgresStore(store.dsn).bind_runner_identity(workspace_id, runner_id)

    with pytest.raises(RuntimeError, match="another workspace runner"):
        store.bind_runner_identity("b" * 32, runner_id)


def test_configured_store_selects_postgres_when_url_is_set(
    store: PostgresStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPPORT_WORKFLOW_DATABASE_URL", store.dsn)

    assert isinstance(configured_store(), PostgresStore)


def test_postgres_run_claims_are_idempotent_and_fenced(store: PostgresStore) -> None:
    now = datetime.now(UTC)
    first = _claim(store, now=now)
    assert first["state"] == "acquired"
    assert _claim(store, now=now + timedelta(seconds=1), owner="worker-two")["state"] == "in_progress"

    reclaimed = _claim(store, now=now + timedelta(seconds=11), owner="worker-two")
    assert reclaimed["run_id"] == first["run_id"]
    assert reclaimed["attempt"] == 2
    assert store.renew_published_run_claim(
        "request-001", "worker-two", now + timedelta(seconds=12), lease_seconds=20
    )
    assert not store.renew_published_run_claim(
        "request-001", "worker-one", now + timedelta(seconds=12), lease_seconds=20
    )

    run = _run(str(first["run_id"]), "JSM-123")
    store.record_execution_step(
        run_id=run.id,
        attempt=2,
        ordinal=0,
        stage_id="triage",
        status="complete",
        elapsed_ms=12,
        recorded_at=now,
    )
    store.save_run(
        run,
        audit_event=(
            run.id,
            run.case_id,
            "decision.approved",
            {"to_status": "approved", "actor_id": "a" * 64, "actor_role": "Approver"},
        ),
        execution_claim=("request-001", "worker-two"),
    )
    assert _claim(store, now=now + timedelta(seconds=13), owner="worker-three")["state"] == "completed"
    assert store.list_execution_steps(run.id)[0]["attempt"] == 2
    events = store.list_run_events(run.id)
    assert len(events) == 1
    assert events[0]["actor_id"] == "a" * 64
    assert events[0]["actor_role"] == "Approver"
    assert store.run_event_outbox_counts() == {"pending": 1, "leased": 0, "delivered": 0}


def test_postgres_outbox_claim_is_multi_worker_safe_and_tokens_are_fenced(store: PostgresStore) -> None:
    case = demo_cases()[0]
    run = _run("RUN-OUTBOX123", case.id)
    store.save_run(
        run,
        audit_event=(run.id, case.id, "workflow.started", {"to_status": "awaiting_human"}),
    )
    now = datetime.now(UTC)

    def claim(owner: str) -> list[dict[str, object]]:
        return store.claim_run_event_outbox(
            lease_owner=owner, now=now, limit=1, lease_seconds=30
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))
    claimed = [event for batch in results for event in batch]
    assert len(claimed) == 1
    event = claimed[0]
    assert event["attempts"] == 1
    token = str(event["lease_token"])
    assert store.acknowledge_run_event_outbox(
        str(event["event_id"]), token, now + timedelta(seconds=1)
    )
    assert not store.acknowledge_run_event_outbox(
        str(event["event_id"]), token, now + timedelta(seconds=2)
    )
    assert store.run_event_outbox_counts() == {"pending": 0, "leased": 0, "delivered": 1}


def test_postgres_outbox_release_retries_without_persisting_sensitive_errors(store: PostgresStore) -> None:
    run = _run("RUN-RELEASE123", demo_cases()[0].id)
    store.save_run(
        run,
        audit_event=(run.id, run.case_id, "workflow.started", {"to_status": "awaiting_human"}),
    )
    now = datetime.now(UTC)
    event = store.claim_run_event_outbox(lease_owner="worker-a", now=now)[0]

    assert store.release_run_event_outbox(
        str(event["event_id"]),
        str(event["lease_token"]),
        now=now + timedelta(seconds=1),
        available_at=now + timedelta(seconds=20),
        error_code="remote_unavailable",
    )
    assert store.claim_run_event_outbox(
        lease_owner="worker-b", now=now + timedelta(seconds=10)
    ) == []
    retried = store.claim_run_event_outbox(
        lease_owner="worker-b", now=now + timedelta(seconds=21)
    )
    assert len(retried) == 1
    assert retried[0]["attempts"] == 2
    assert "error" not in retried[0]

