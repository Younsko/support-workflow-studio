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
