"""Opt-in, metadata-only runner heartbeat to the workspace control plane."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

_RUNNER_ID = re.compile(r"^[a-f0-9]{32}$")


class RunnerHeartbeatConfigurationError(RuntimeError):
    """The optional runner heartbeat settings are incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class RunnerHeartbeatConfig:
    api_url: str
    runner_id: str
    token: str
    interval_seconds: int

    @classmethod
    def from_env(cls) -> RunnerHeartbeatConfig | None:
        token = os.getenv("SUPPORT_CONTROL_PLANE_RUNNER_TOKEN", "").strip()
        if not token:
            return None
        return cls.from_values(
            os.getenv("SUPPORT_CONTROL_PLANE_API_URL", ""),
            os.getenv("SUPPORT_RUNNER_ID", ""),
            token,
            os.getenv("SUPPORT_RUNNER_HEARTBEAT_INTERVAL_SECONDS", "60"),
        )

    @classmethod
    def from_values(
        cls, api_url: str, runner_id: str, token: str, interval_seconds: str | int
    ) -> RunnerHeartbeatConfig:
        token = token.strip()
        if len(token) < 32 or len(token) > 128:
            raise RunnerHeartbeatConfigurationError(
                "The control-plane runner credential must contain 32 to 128 characters."
            )

        api_url = api_url.strip()
        parsed = urlsplit(api_url)
        try:
            loopback = bool(parsed.hostname) and (
                ipaddress.ip_address(parsed.hostname).is_loopback
                if parsed.hostname != "localhost"
                else True
            )
        except ValueError:
            loopback = False
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        ):
            raise RunnerHeartbeatConfigurationError(
                "The control-plane API URL must use HTTPS, or HTTP on loopback, without a path or credentials."
            )

        runner_id = runner_id.strip()
        if not _RUNNER_ID.fullmatch(runner_id):
            raise RunnerHeartbeatConfigurationError(
                "A valid enrolled runner ID is required for control-plane heartbeats."
            )
        try:
            interval = int(interval_seconds)
        except ValueError as exc:
            raise RunnerHeartbeatConfigurationError(
                "The runner heartbeat interval must be an integer from 30 to 3600 seconds."
            ) from exc
        if not 30 <= interval <= 3600:
            raise RunnerHeartbeatConfigurationError(
                "The runner heartbeat interval must be between 30 and 3600 seconds."
            )
        return cls(api_url.rstrip("/"), runner_id, token, interval)


def initial_heartbeat_state() -> dict[str, object]:
    return {
        "enabled": False,
        "status": "not_configured",
        "last_attempt_at": None,
        "last_success_at": None,
        "interval_seconds": None,
        "last_error": None,
    }


async def heartbeat_once(
    config: RunnerHeartbeatConfig,
    state: dict[str, object],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Send one credential-redacted heartbeat and update non-secret status."""
    state.update(
        enabled=True,
        status="checking",
        last_attempt_at=datetime.now(UTC).isoformat(),
        interval_seconds=config.interval_seconds,
        last_error=None,
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = await client.post(
                f"{config.api_url}/api/v1/runners/{config.runner_id}/heartbeat",
                headers={"X-Runner-Token": config.token},
            )
    except httpx.HTTPError:
        state.update(status="unreachable", last_error="Control plane is unreachable.")
        return

    if response.status_code in {401, 403}:
        state.update(
            status="credential_rejected",
            last_error="The runner credential was rejected; reconnect this runner.",
        )
        return
    if response.status_code != 200:
        state.update(
            status="unavailable",
            last_error=f"Control plane returned HTTP {response.status_code}.",
        )
        return
    state.update(
        status="healthy",
        last_success_at=datetime.now(UTC).isoformat(),
        last_error=None,
    )


async def heartbeat_loop(state: dict[str, object]) -> None:
    """Keep running across transient control-plane failures and config changes."""
    while True:
        try:
            config = RunnerHeartbeatConfig.from_env()
            if config is None:
                state.update(initial_heartbeat_state())
                delay = 15
            else:
                await heartbeat_once(config, state)
                delay = config.interval_seconds
        except RunnerHeartbeatConfigurationError:
            state.update(
                enabled=False,
                status="misconfigured",
                interval_seconds=None,
                last_error="Control-plane runner settings are incomplete or invalid.",
            )
            delay = 15
        await asyncio.sleep(delay)
