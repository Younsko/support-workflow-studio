from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from app import workflow
from app.itsm import (
    JiraConfig,
    JiraConfigurationError,
    JiraOutcomeUnknown,
    list_queue_cases,
)
from app.main import CASES, _validate_private_local_mode, app
from app.providers import ProviderConfig
from app.schemas import CaseRecord
from app.workflow import _support_level
from fastapi.testclient import TestClient

client = TestClient(app)
TEST_KNOWLEDGE_DIR = Path(__file__).with_name("fixtures") / "knowledge"


def configure_test_knowledge(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_DIR", str(TEST_KNOWLEDGE_DIR))


def setup_function() -> None:
    from app.fixtures import demo_cases
    from app.main import RUNS

    RUNS.clear()
    CASES.clear()
    CASES.update({case.id: case for case in demo_cases()})


def test_demo_run_keeps_distinct_review_lanes_and_requires_human_decision(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    response = client.post(
        "/api/workflows", json={"case_id": "SUP-4821", "candidate_count": 2}
    )
    assert response.status_code == 201
    run = response.json()
    assert run["provider_mode"] == "demo"
    assert run["status"] == "awaiting_human"
    assert run["score"] >= 9
    assert run["support_level"] in {"N1", "N2", "N3", "N3+"}
    assert run["support_route_reason"]
    assert {item["lane"] for item in run["reviewer_findings"]} == {
        "Technical correctness",
        "Security & privacy",
        "Customer clarity",
    }
    assert all(
        item["status"] in {"complete", "review", "pending"} for item in run["stages"]
    )
    assert "no model calls" in run["model_name"].lower()
    approval = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"]},
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "approved"
    assert "not sent" in approval.json()["human_decision"].lower()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("How do I update my password?", "N1"),
        ("My VPN disconnects after connecting", "N2"),
        ("The application has a recurring crash affecting several teammates", "N3"),
        ("Production service unavailable for all users", "N3+"),
    ],
)
def test_support_level_is_a_provisional_n1_to_n3plus_route(text, expected) -> None:
    level, reason = _support_level(text)

    assert level == expected
    assert reason


def _configure_private_local_mode(monkeypatch) -> None:
    monkeypatch.setenv("PRIVATE_LOCAL_MODE", "true")
    monkeypatch.setenv("MODEL_PROVIDER", "ollama-local")
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("MODEL_NAME", "synthetic-local-model")
    monkeypatch.delenv("JSM_BASE_URL", raising=False)
    monkeypatch.delenv("JSM_USER_EMAIL", raising=False)
    monkeypatch.delenv("JSM_API_TOKEN", raising=False)
    monkeypatch.delenv("JSM_SERVICE_DESK_ID", raising=False)
    monkeypatch.delenv("JSM_QUEUE_ID", raising=False)
    monkeypatch.setenv("ALLOW_EXTERNAL_TICKET_INFERENCE", "false")
    monkeypatch.delenv("JSM_AUTO_TRIAGE_NEW_TICKETS", raising=False)
    for name in (
        "COUNCIL_PROVIDER",
        "COUNCIL_MODEL",
        "COUNCIL_API_KEY",
        "COUNCIL_BASE_URL",
        "JUDGE_PROVIDER",
        "JUDGE_MODEL",
        "JUDGE_API_KEY",
        "JUDGE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_private_local_mode_fails_closed_for_nonlocal_provider(monkeypatch) -> None:
    monkeypatch.setenv("PRIVATE_LOCAL_MODE", "true")
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-secret")
    monkeypatch.setenv("MODEL_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("MODEL_NAME", "synthetic-model")

    with pytest.raises(RuntimeError, match="requires MODEL_PROVIDER=ollama-local"):
        _validate_private_local_mode()


def test_private_local_mode_rejects_jira_and_external_council(monkeypatch) -> None:
    _configure_private_local_mode(monkeypatch)
    monkeypatch.setenv("JSM_BASE_URL", "https://synthetic.atlassian.net")
    with pytest.raises(RuntimeError, match="refuses Jira connector configuration"):
        _validate_private_local_mode()

    _configure_private_local_mode(monkeypatch)
    monkeypatch.setenv("COUNCIL_PROVIDER", "openai-compatible")
    with pytest.raises(RuntimeError, match="refuses external inference"):
        _validate_private_local_mode()

    _configure_private_local_mode(monkeypatch)
    monkeypatch.setenv("ALLOW_EXTERNAL_TICKET_INFERENCE", "true")
    with pytest.raises(RuntimeError, match="refuses external inference"):
        _validate_private_local_mode()

    _configure_private_local_mode(monkeypatch)
    monkeypatch.setenv("JSM_AUTO_TRIAGE_NEW_TICKETS", "true")
    with pytest.raises(RuntimeError, match="refuses external inference"):
        _validate_private_local_mode()


def test_private_local_mode_blocks_jira_sync_and_model_council(monkeypatch) -> None:
    _configure_private_local_mode(monkeypatch)
    _validate_private_local_mode()

    sync = client.post("/api/itsm/sync")
    council = client.post(
        "/api/workflows",
        json={
            "case_id": "SUP-4821",
            "candidate_count": 1,
            "include_model_council": True,
        },
    )
    publish = client.post("/api/workflows/unknown/publish-ticket-reply")
    status = client.get("/api/integrations/status").json()

    assert sync.status_code == 403
    assert council.status_code == 403
    assert publish.status_code == 403
    assert status["private_local_mode"] is True
    assert status["jira_configured"] is False
    assert status["external_ticket_inference_allowed"] is False


def test_model_cannot_downgrade_deterministic_n3plus_route(monkeypatch) -> None:
    async def suggest_low_tier(*_args, **_kwargs) -> str:
        return json.dumps(
            {
                "category": "Synthetic service issue",
                "urgency": "Low",
                "support_level": "N1",
                "confidence": 0.99,
                "unknowns": [],
            }
        )

    monkeypatch.setattr(workflow, "_model_call", suggest_low_tier)
    case = CaseRecord(
        id="LAB-PRIORITY",
        title="Synthetic broad outage",
        requester="Synthetic Reporter",
        channel="Lab",
        created_at="2026-01-01T00:00:00Z",
        priority="Urgent",
        category="Test",
        status="New",
        body="Production service unavailable for all users.",
        impact="Reported broad production impact; verification pending",
        tags=["synthetic"],
    )
    config = ProviderConfig(
        provider="openai-compatible",
        model="synthetic-model",
        api_key="synthetic-secret",
        base_url="https://synthetic.example/v1",
        timeout=5,
        max_calls=20,
    )

    result = asyncio.run(
        workflow.run_workflow(case, config, candidate_count=1)
    )

    assert result.support_level == "N3+"
    assert "broad service impact" in result.support_route_reason


def test_security_case_preserves_secret_boundary(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    response = client.post(
        "/api/workflows", json={"case_id": "SUP-4823", "candidate_count": 1}
    )
    assert response.status_code == 201
    run = response.json()
    assert "do not share" in run["draft"].lower()
    assert (
        "recovery code" not in run["draft"].lower()
        or "do not share passwords or recovery codes" in run["draft"].lower()
    )
    assert run["status"] == "awaiting_human"


def test_prompt_injection_is_treated_as_untrusted_ticket_text(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    response = client.post(
        "/api/workflows", json={"case_id": "SUP-4824", "candidate_count": 1}
    )
    assert response.status_code == 201
    run = response.json()
    assert "hidden prompt" not in run["draft"].lower()
    assert "ask the employee for their password" not in run["draft"].lower()
    assert run["status"] == "awaiting_human"


def test_reject_is_terminal_and_decision_cannot_be_replayed(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    run = client.post(
        "/api/workflows", json={"case_id": "SUP-4822", "candidate_count": 1}
    ).json()
    rejected = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "reject", "expected_version": run["approval_version"]},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    replay = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"]},
    )
    assert replay.status_code == 409


def test_stale_human_decision_is_rejected_without_changing_the_run(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    run = client.post(
        "/api/workflows", json={"case_id": "SUP-4821", "candidate_count": 1}
    ).json()

    stale = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"] + 1},
    )
    assert stale.status_code == 409
    assert "draft changed" in stale.json()["detail"].lower()

    current = client.get(f"/api/workflows/latest/{run['case_id']}").json()
    assert current["status"] == "awaiting_human"
    assert current["approval_version"] == run["approval_version"]

    approved = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": current["approval_version"]},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_jira_connector_rejects_untrusted_hosts_and_plain_http() -> None:
    invalid_configs = [
        (
            "https://attacker.example",
            "atlassian.net",
        ),
        (
            "http://example.atlassian.net",
            "HTTPS",
        ),
    ]
    for base_url, expected in invalid_configs:
        config = JiraConfig(
            base_url=base_url,
            email="agent@example.com",
            api_token="synthetic-token",
            service_desk_id="1",
            queue_id="2",
        )
        with pytest.raises(JiraConfigurationError, match=expected):
            config.validate()


def test_ticket_comment_requires_two_separate_human_actions(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    configure_test_knowledge(monkeypatch)
    monkeypatch.setenv("JSM_BASE_URL", "https://demo.atlassian.net")
    monkeypatch.setenv("JSM_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("JSM_API_TOKEN", "synthetic-token")
    monkeypatch.setenv("JSM_SERVICE_DESK_ID", "1")
    monkeypatch.setenv("JSM_QUEUE_ID", "2")
    CASES["DEMO-81"] = CaseRecord(
        id="DEMO-81",
        external_key="DEMO-81",
        source="jira-service-management",
        title="VPN connection drops",
        requester="Synthetic Requester",
        channel="Jira Service Management",
        created_at="2026-11-22T09:00:00Z",
        priority="High",
        category="Connectivity",
        status="Open",
        body="The VPN disconnected during a support call. Please advise.",
        impact="Synthetic single-user example",
    )

    ticket_details = {
        "summary": "VPN connection drops",
        "description": "The VPN disconnected during a support call. Please advise.",
    }

    async def get_details(_config, _issue_key):
        return dict(ticket_details)

    published = []

    async def create_comment(_config, issue_key, body):
        published.append((issue_key, body))
        return {"id": "synthetic-comment-1", "public": "True"}

    from app import main

    monkeypatch.setattr(main, "get_request_details", get_details)
    monkeypatch.setattr(main, "create_public_comment", create_comment)
    run = client.post(
        "/api/workflows", json={"case_id": "DEMO-81", "candidate_count": 1}
    ).json()

    early_publish = client.post(f"/api/workflows/{run['id']}/publish-ticket-reply")
    assert early_publish.status_code == 409
    assert published == []

    approval = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"]},
    )
    assert approval.status_code == 200

    ticket_details["description"] = "The ticket changed after review."
    stale_publish = client.post(f"/api/workflows/{run['id']}/publish-ticket-reply")
    assert stale_publish.status_code == 409
    assert "changed after" in stale_publish.json()["detail"].lower()
    assert published == []

    ticket_details["description"] = (
        "The VPN disconnected during a support call. Please advise."
    )
    sent = client.post(f"/api/workflows/{run['id']}/publish-ticket-reply")
    assert sent.status_code == 200
    assert sent.json()["public"] is True
    assert published and published[0][0] == "DEMO-81"


def test_jira_queue_read_maps_only_bounded_queue_fields(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "values": [
                    {
                        "key": "HELP-19",
                        "fields": {
                            "summary": "Synthetic app crash",
                            "description": {
                                "type": "doc",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "Crash after update",
                                            }
                                        ],
                                    }
                                ],
                            },
                            "reporter": {"displayName": "Sample Person"},
                            "priority": {"name": "High"},
                            "status": {"name": "Waiting for support"},
                        },
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, path, params):
            assert path.endswith("/queue/2/issue")
            assert params == {"start": 0, "limit": 50}
            return FakeResponse()

    from app import itsm

    monkeypatch.setattr(itsm, "_http_client", lambda _config: FakeClient())
    config = JiraConfig(
        base_url="https://demo.atlassian.net",
        email="agent@example.com",
        api_token="synthetic-token",
        service_desk_id="1",
        queue_id="2",
    )
    import asyncio

    cases = asyncio.run(list_queue_cases(config, limit=500))
    assert len(cases) == 1
    assert cases[0].external_key == "HELP-19"
    assert cases[0].body == "Crash after update"
    assert cases[0].source == "jira-service-management"


def test_live_model_inference_for_jira_ticket_is_opt_in(monkeypatch) -> None:
    configure_test_knowledge(monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MODEL_NAME", "synthetic-model")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-key")
    monkeypatch.setenv("MODEL_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("ALLOW_EXTERNAL_TICKET_INFERENCE", "false")
    CASES["DEMO-82"] = CaseRecord(
        id="DEMO-82",
        external_key="DEMO-82",
        source="jira-service-management",
        title="Synthetic sign-in request",
        requester="Synthetic Requester",
        channel="Jira Service Management",
        created_at="2026-11-22T09:00:00Z",
        priority="Normal",
        category="Identity",
        status="Open",
        body="Synthetic issue with a fictional sign-in error.",
        impact="One synthetic user",
    )

    async def get_details(_config, _issue_key):
        return {
            "summary": "Synthetic sign-in request",
            "description": "Synthetic issue.",
        }

    from app import main

    monkeypatch.setattr(main, "get_request_details", get_details)
    response = client.post(
        "/api/workflows", json={"case_id": "DEMO-82", "candidate_count": 1}
    )
    assert response.status_code == 403
    assert "ALLOW_EXTERNAL_TICKET_INFERENCE" in response.json()["detail"]


def test_unknown_jira_comment_outcome_blocks_automatic_retry(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    configure_test_knowledge(monkeypatch)
    monkeypatch.setenv("JSM_BASE_URL", "https://demo.atlassian.net")
    monkeypatch.setenv("JSM_USER_EMAIL", "agent@example.com")
    monkeypatch.setenv("JSM_API_TOKEN", "synthetic-token")
    monkeypatch.setenv("JSM_SERVICE_DESK_ID", "1")
    monkeypatch.setenv("JSM_QUEUE_ID", "2")
    CASES["DEMO-83"] = CaseRecord(
        id="DEMO-83",
        external_key="DEMO-83",
        source="jira-service-management",
        title="Synthetic VPN disconnect",
        requester="Synthetic Requester",
        channel="Jira Service Management",
        created_at="2026-11-22T09:00:00Z",
        priority="High",
        category="Connectivity",
        status="Open",
        body="VPN disconnected during a synthetic support call.",
        impact="Synthetic single-user example",
    )

    async def get_details(_config, _issue_key):
        return {
            "summary": "Synthetic VPN disconnect",
            "description": "VPN disconnected.",
        }

    call_count = 0

    async def uncertain_comment(_config, _issue_key, _body):
        nonlocal call_count
        call_count += 1
        raise JiraOutcomeUnknown(
            "Jira comment outcome may be unknown; inspect the ticket."
        )

    from app import main

    monkeypatch.setattr(main, "get_request_details", get_details)
    monkeypatch.setattr(main, "create_public_comment", uncertain_comment)
    run = client.post(
        "/api/workflows", json={"case_id": "DEMO-83", "candidate_count": 1}
    ).json()
    client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"]},
    )

    first_attempt = client.post(f"/api/workflows/{run['id']}/publish-ticket-reply")
    second_attempt = client.post(f"/api/workflows/{run['id']}/publish-ticket-reply")
    assert first_attempt.status_code == 502
    assert second_attempt.status_code == 409
    assert call_count == 1

def test_plan_critic_requires_structured_latest_score_and_review_arrays() -> None:
    score, findings, missing, valid = workflow._plan_critic_result(
        'analysis {"score": 8.7, "findings": ["Needs exact error"], "missing_checks": ["Confirm timestamp"]} trailing text'
    )
    assert valid is True
    assert score == 8.7
    assert findings == ["Needs exact error"]
    assert missing == ["Confirm timestamp"]

    invalid = workflow._plan_critic_result("no structured review")
    assert invalid[0] == 0.0
    assert invalid[3] is False


def test_plan_safety_gate_blocks_unapproved_network_probe() -> None:
    evidence = [{"id": "KB-104", "title": "VPN checks", "body": "synthetic"}]
    findings = workflow._plan_safety_findings(
        ["Use KB-104, then ping 8.8.8.8 to check connectivity and verify the reported error."],
        evidence,
    )
    assert any("unapproved network probe" in item for item in findings)

    public_probe = workflow._plan_safety_findings(
        ["Open a public website such as https://www.example.com to verify internet access."],
        evidence,
    )
    assert any("connectivity probe" in item for item in public_probe)

    unicode_citation = workflow._plan_safety_findings(
        ["Reported VPN failure remains unverified. Use KB–104 and ask for exact error details; route to a specialist."],
        evidence,
    )
    assert unicode_citation == []


def test_vpn_safe_fallback_plan_passes_deterministic_checks() -> None:
    from app.fixtures import demo_cases

    evidence = [{"id": "KB-104", "title": "VPN connection checks", "body": "synthetic"}]
    case = next(item for item in demo_cases() if item.id == "SUP-4821")
    plan = workflow._build_plan(case, evidence)

    assert len(plan) == 5
    assert workflow._plan_safety_findings(plan, evidence) == []


def test_demo_run_exposes_plan_revise_then_pass_history(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    response = client.post("/api/workflows", json={"case_id": "SUP-4821", "candidate_count": 1})
    assert response.status_code == 201
    run = response.json()
    assert [item["verdict"] for item in run["plan_reviews"]] == ["revise", "pass"]
    assert run["plan_reviews"][-1]["score"] >= 9


def test_n1_password_reset_uses_approved_synthetic_guidance_and_can_be_approved(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    response = client.post(
        "/api/workflows",
        json={"case_id": "SUP-4825", "candidate_count": 1},
    )

    assert response.status_code == 201
    run = response.json()
    assert run["support_level"] == "N1"
    assert any(item["id"] == "KB-410" for item in run["evidence"])
    assert run["score"] >= 9
    assert "do not share" in run["draft"].lower()

    approved = client.post(
        f"/api/workflows/{run['id']}/decision",
        json={"decision": "approve", "expected_version": run["approval_version"]},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["ticket_comment_published"] is False


def test_n1_customer_reply_can_pass_without_internal_citation_or_incident_disclaimer() -> None:
    from app.fixtures import demo_cases

    case = next(item for item in demo_cases() if item.id == "SUP-4825")
    evidence = [{"id": "KB-410", "title": "Password reset self-service", "body": "Use the approved self-service account portal. Do not invent a URL."}]
    natural_reply = (
        "Hi Taylor, use your organization's approved self-service account portal "
        "to reset your password. If it shows an error, send the exact error wording "
        "without credentials and we will route it to identity support."
    )

    assert workflow._draft_hard_fail(case, natural_reply, evidence) is False
    assert workflow._draft_hard_fail(case, natural_reply + " Visit https://invented.example", evidence) is True
    assert workflow._draft_hard_fail(case, "Please send your password.", evidence) is True
