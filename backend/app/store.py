"""Local durable state for the single-process support workflow prototype."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .schemas import CaseRecord, WorkflowResult


class LocalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS monitor_state (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def load_cases(self) -> dict[str, CaseRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, payload FROM cases ORDER BY rowid").fetchall()
        return {row[0]: CaseRecord.model_validate_json(row[1]) for row in rows}

    def load_runs(self) -> dict[str, WorkflowResult]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, payload FROM runs ORDER BY rowid").fetchall()
        return {row[0]: WorkflowResult.model_validate_json(row[1]) for row in rows}

    def save_case(self, case: CaseRecord) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO cases(id, payload) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (case.id, case.model_dump_json()),
            )
            connection.commit()

    def save_run(self, run: WorkflowResult) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO runs(id, payload) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (run.id, run.model_dump_json()),
            )
            connection.commit()

    def load_monitor_state(self) -> dict[str, object]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM monitor_state WHERE id = 'jira'"
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row[0])
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_monitor_state(self, state: dict[str, object]) -> None:
        payload = json.dumps(state, separators=(",", ":"), sort_keys=True)
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO monitor_state(id, payload) VALUES ('jira', ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (payload,),
            )
            connection.commit()


def configured_store_path() -> Path:
    default_name = "private-state.sqlite3" if os.getenv("PRIVATE_LOCAL_MODE", "").lower() in {"1", "true", "yes"} else "state.sqlite3"
    default_path = Path(__file__).resolve().parents[2] / ".local" / default_name
    return Path(os.getenv("SUPPORT_WORKFLOW_DB_PATH", str(default_path))).expanduser()


def configured_store() -> LocalStore:
    return LocalStore(configured_store_path())
