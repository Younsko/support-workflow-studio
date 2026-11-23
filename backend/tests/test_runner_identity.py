from __future__ import annotations

import pytest
from app.runner_identity import RunnerIdentity


def test_runner_identity_is_optional_for_local_prototype(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPPORT_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("SUPPORT_RUNNER_ID", raising=False)

    assert RunnerIdentity.from_env() is None


def test_runner_identity_requires_both_control_plane_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPPORT_WORKSPACE_ID", "a" * 32)
    monkeypatch.delenv("SUPPORT_RUNNER_ID", raising=False)

    with pytest.raises(RuntimeError, match="must both be configured"):
        RunnerIdentity.from_env()


def test_runner_identity_accepts_control_plane_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPPORT_WORKSPACE_ID", "a" * 32)
    monkeypatch.setenv("SUPPORT_RUNNER_ID", "b" * 32)

    assert RunnerIdentity.from_env() == RunnerIdentity("a" * 32, "b" * 32)


def test_runner_identity_rejects_malformed_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPPORT_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("SUPPORT_RUNNER_ID", "runner-1")

    with pytest.raises(RuntimeError, match="control-plane IDs"):
        RunnerIdentity.from_env()
