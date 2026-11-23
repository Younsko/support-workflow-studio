from __future__ import annotations

import httpx
import pytest
from app.runner_heartbeat import (
    RunnerHeartbeatConfig,
    RunnerHeartbeatConfigurationError,
    heartbeat_once,
    initial_heartbeat_state,
)


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPPORT_CONTROL_PLANE_API_URL", "https://control.example.test")
    monkeypatch.setenv("SUPPORT_RUNNER_ID", "a" * 32)
    monkeypatch.setenv("SUPPORT_CONTROL_PLANE_RUNNER_TOKEN", "test-runner-token-" + "x" * 32)


def test_heartbeat_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPPORT_CONTROL_PLANE_RUNNER_TOKEN", raising=False)

    assert RunnerHeartbeatConfig.from_env() is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SUPPORT_CONTROL_PLANE_API_URL", "http://control.example.test"),
        ("SUPPORT_CONTROL_PLANE_API_URL", "https://control.example.test/path"),
        ("SUPPORT_CONTROL_PLANE_API_URL", "https://control.example.test:invalid"),
        ("SUPPORT_RUNNER_ID", "not-a-runner-id"),
        ("SUPPORT_CONTROL_PLANE_RUNNER_TOKEN", "token-with-newline\r\nInjected: true"),
        ("SUPPORT_RUNNER_HEARTBEAT_INTERVAL_SECONDS", "10"),
        ("SUPPORT_RUNNER_HEARTBEAT_INTERVAL_SECONDS", "many"),
    ],
)
def test_heartbeat_rejects_invalid_settings(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RunnerHeartbeatConfigurationError):
        RunnerHeartbeatConfig.from_env()


@pytest.mark.asyncio
async def test_heartbeat_sends_only_runner_metadata_and_hides_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    config = RunnerHeartbeatConfig.from_env()
    assert config is not None
    observed: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={"runner_id": "a" * 32, "last_heartbeat_at": "2026-11-23T00:00:00Z"},
        )

    state = initial_heartbeat_state()
    await heartbeat_once(config, state, transport=httpx.MockTransport(respond))

    assert state["status"] == "healthy"
    assert state["enabled"] is True
    assert "test-runner-token" not in repr(state)
    assert len(observed) == 1
    assert observed[0].method == "POST"
    assert observed[0].url == "https://control.example.test/api/v1/runners/" + "a" * 32 + "/heartbeat"
    assert observed[0].headers["x-runner-token"] == config.token
    assert observed[0].content == b""


@pytest.mark.asyncio
async def test_heartbeat_marks_revoked_credentials_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    config = RunnerHeartbeatConfig.from_env()
    assert config is not None
    state = initial_heartbeat_state()
    transport = httpx.MockTransport(lambda _request: httpx.Response(401))

    await heartbeat_once(config, state, transport=transport)

    assert state["status"] == "credential_rejected"
    assert state["last_error"] == "The runner credential was rejected; reconnect this runner."
    assert "test-runner-token" not in repr(state)
