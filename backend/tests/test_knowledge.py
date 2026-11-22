from __future__ import annotations

import json

import pytest
from app.knowledge import KnowledgeConfigurationError, knowledge_status, retrieve


def _approval_record(path: str) -> dict[str, str]:
    return {
        "path": path,
        "approval_status": "approved",
        "owner_ref": "synthetic-test-owner",
        "approved_at": "2026-11-22",
        "approval_evidence_ref": "synthetic-fixture",
    }


def _write_manifest(directory, approved_sources: list[dict[str, str]]) -> None:
    (directory / ".support-workflow-approved-sources.json").write_text(
        json.dumps({"version": 1, "approved_sources": approved_sources}),
        encoding="utf-8",
    )


def test_local_retrieval_reads_only_manifest_allowlisted_files(tmp_path, monkeypatch) -> None:
    (tmp_path / "approved.md").write_text(
        "# Approved VPN guidance\nConfirm the VPN error and timestamp.",
        encoding="utf-8",
    )
    (tmp_path / "unlisted.md").write_text(
        "# Unlisted private notes\nDo not load this content.",
        encoding="utf-8",
    )
    _write_manifest(tmp_path, [_approval_record("approved.md")])
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    status = knowledge_status()
    results = retrieve("VPN error")

    assert status["available"] is True
    assert status["source_count"] == 1
    assert "Allowlisted" in status["message"]
    assert len(results) == 1
    assert results[0]["title"] == "Approved VPN guidance"
    assert "unlisted private notes" not in results[0]["body"].lower()


def test_synthetic_retrieval_uses_exact_domain_terms_and_avoids_substrings(monkeypatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_DIR", raising=False)

    vpn = retrieve("VPN disconnects during calls; Gateway timeout")
    password = retrieve("How do I reset my password before training?")
    unrelated = retrieve("Qwerty zxvbn unique synthetic request")

    assert [item["id"] for item in vpn] == ["KB-104"]
    assert [item["id"] for item in password] == ["KB-410"]
    assert unrelated == []


def test_local_knowledge_without_manifest_is_unavailable(tmp_path, monkeypatch) -> None:
    (tmp_path / "guide.md").write_text("# Guidance\nText", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    status = knowledge_status()

    assert status["available"] is False
    assert status["source_count"] == 0
    assert ".support-workflow-approved-sources.json" in status["message"]
    with pytest.raises(KnowledgeConfigurationError, match="allowlist"):
        retrieve("Guidance")


def test_local_knowledge_rejects_path_traversal_in_manifest(tmp_path, monkeypatch) -> None:
    _write_manifest(tmp_path, [_approval_record("../outside.md")])
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    status = knowledge_status()

    assert status["available"] is False
    assert "unsafe" in status["message"]


def test_local_knowledge_rejects_missing_allowlisted_source(tmp_path, monkeypatch) -> None:
    _write_manifest(tmp_path, [_approval_record("missing.md")])
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    with pytest.raises(KnowledgeConfigurationError, match="missing or out-of-folder"):
        retrieve("support issue")


def test_local_knowledge_rejects_unapproved_source_record(tmp_path, monkeypatch) -> None:
    (tmp_path / "review-pending.md").write_text("# Pending\nText", encoding="utf-8")
    record = _approval_record("review-pending.md")
    record["approval_status"] = "unverified"
    _write_manifest(tmp_path, [record])
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path))

    status = knowledge_status()

    assert status["available"] is False
    assert "without complete approval metadata" in status["message"]
