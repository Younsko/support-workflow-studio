from __future__ import annotations

from fastapi.testclient import TestClient

from app import main
from app.fixtures import demo_cases
from app.store import LocalStore


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
