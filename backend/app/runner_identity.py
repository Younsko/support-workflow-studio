"""Immutable workspace binding for a dedicated customer runner store."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

_RESOURCE_ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True)
class RunnerIdentity:
    workspace_id: str
    runner_id: str

    @classmethod
    def from_env(cls) -> RunnerIdentity | None:
        workspace_id = os.getenv("SUPPORT_WORKSPACE_ID", "").strip()
        runner_id = os.getenv("SUPPORT_RUNNER_ID", "").strip()
        if not workspace_id and not runner_id:
            return None
        if not _RESOURCE_ID.fullmatch(workspace_id) or not _RESOURCE_ID.fullmatch(runner_id):
            raise RuntimeError(
                "SUPPORT_WORKSPACE_ID and SUPPORT_RUNNER_ID must both be configured as control-plane IDs."
            )
        return cls(workspace_id=workspace_id, runner_id=runner_id)
