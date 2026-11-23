from __future__ import annotations

import hashlib
import json


def jira_ticket_fingerprint(summary: str, description: str, status: str) -> str:
    """Fingerprint normalized source fields before any model-input truncation."""
    payload = json.dumps(
        [summary, description, status], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
