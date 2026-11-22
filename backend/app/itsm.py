from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .schemas import CaseRecord


class JiraConfigurationError(RuntimeError):
    pass


class JiraRequestError(RuntimeError):
    pass


class JiraOutcomeUnknown(JiraRequestError):
    """Jira may have accepted a write, so the caller must not retry blindly."""


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    service_desk_id: str
    queue_id: str

    @classmethod
    def from_env(cls) -> JiraConfig:
        return cls(
            base_url=os.getenv("JSM_BASE_URL", "").strip().rstrip("/"),
            email=os.getenv("JSM_USER_EMAIL", "").strip(),
            api_token=os.getenv("JSM_API_TOKEN", "").strip(),
            service_desk_id=os.getenv("JSM_SERVICE_DESK_ID", "").strip(),
            queue_id=os.getenv("JSM_QUEUE_ID", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.base_url
            and self.email
            and self.api_token
            and self.service_desk_id
            and self.queue_id
        )

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        try:
            port = parsed.port
        except ValueError as exc:
            raise JiraConfigurationError("Jira Cloud URL has an invalid port.") from exc
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise JiraConfigurationError(
                "Jira Cloud URL must be an HTTPS site URL without embedded credentials."
            )
        if host != "atlassian.net" and not host.endswith(".atlassian.net"):
            raise JiraConfigurationError(
                "This connector currently supports Jira Cloud sites on atlassian.net only."
            )
        if not self.service_desk_id.isdigit() or not self.queue_id.isdigit():
            raise JiraConfigurationError(
                "Service desk ID and queue ID must be numeric values from Jira."
            )


def _extract_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(filter(None, (_extract_text(item) for item in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        ignored_keys = {"type", "version", "attrs"}
        return " ".join(
            filter(
                None,
                (
                    _extract_text(item)
                    for key, item in value.items()
                    if key not in ignored_keys
                ),
            )
        )
    return ""


def _http_client(config: JiraConfig) -> httpx.AsyncClient:
    config.validate()
    return httpx.AsyncClient(
        base_url=config.base_url,
        auth=(config.email, config.api_token),
        headers={"Accept": "application/json"},
        timeout=20,
        follow_redirects=False,
    )


async def list_queue_cases(
    config: JiraConfig, limit: int = 50, start: int = 0
) -> list[CaseRecord]:
    if not config.configured:
        raise JiraConfigurationError(
            "Jira Service Management connection is not configured."
        )
    safe_limit = max(1, min(50, limit))
    safe_start = max(0, start)
    path = f"/rest/servicedeskapi/servicedesk/{config.service_desk_id}/queue/{config.queue_id}/issue"
    try:
        async with _http_client(config) as client:
            response = await client.get(
                path, params={"start": safe_start, "limit": safe_limit}
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise JiraRequestError(f"Jira queue read failed: {type(exc).__name__}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        raise JiraRequestError("Jira queue response has an unsupported shape.")

    results: list[CaseRecord] = []
    for item in payload["values"]:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            continue
        issue_key = str(item.get("key", "")).strip()
        if not issue_key:
            continue
        reporter = fields.get("reporter") or {}
        priority = fields.get("priority") or {}
        status = fields.get("status") or {}
        issue_type = fields.get("issuetype") or {}
        reporter = reporter if isinstance(reporter, dict) else {}
        priority = priority if isinstance(priority, dict) else {}
        status = status if isinstance(status, dict) else {}
        issue_type = issue_type if isinstance(issue_type, dict) else {}
        created = str(fields.get("created", ""))
        results.append(
            CaseRecord(
                id=issue_key,
                external_key=issue_key,
                source="jira-service-management",
                title=str(fields.get("summary") or issue_key)[:120],
                requester=str(reporter.get("displayName") or "Jira requester")[:80],
                channel="Jira Service Management",
                created_at=created,
                priority=str(priority.get("name") or "Untriaged")[:30],
                category=str(issue_type.get("name") or "Service request")[:80],
                status=str(status.get("name") or "Open")[:50],
                body=_extract_text(fields.get("description"))[:4000],
                impact="Impact not verified",
                tags=["jira-cloud", "external-ticket"],
            )
        )
    return results


async def get_request_details(config: JiraConfig, issue_key: str) -> dict[str, object]:
    if not config.configured:
        raise JiraConfigurationError(
            "Jira Service Management connection is not configured."
        )
    if (
        not issue_key
        or len(issue_key) > 80
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in issue_key
        )
    ):
        raise JiraConfigurationError("Ticket key contains unsupported characters.")
    try:
        async with _http_client(config) as client:
            response = await client.get(f"/rest/servicedeskapi/request/{issue_key}")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise JiraRequestError(
            f"Jira ticket read failed: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(
        payload.get("requestFieldValues"), list
    ):
        raise JiraRequestError("Jira request response has an unsupported shape.")
    request_values = payload["requestFieldValues"]
    summary = next(
        (
            item.get("value")
            for item in request_values
            if isinstance(item, dict) and item.get("fieldId") == "summary"
        ),
        issue_key,
    )
    description = next(
        (
            item.get("value")
            for item in request_values
            if isinstance(item, dict) and item.get("fieldId") == "description"
        ),
        "",
    )
    current_status = payload.get("currentStatus")
    current_status = current_status if isinstance(current_status, dict) else {}
    return {
        "summary": str(summary)[:120],
        "description": _extract_text(description)[:4000],
        "status": str(current_status.get("status") or ""),
        "status_category": str(current_status.get("statusCategory") or ""),
    }


async def create_public_comment(
    config: JiraConfig, issue_key: str, body: str
) -> dict[str, str]:
    if not config.configured:
        raise JiraConfigurationError(
            "Jira Service Management connection is not configured."
        )
    if not body.strip() or len(body) > 10000:
        raise JiraConfigurationError("Comment must contain 1 to 10,000 characters.")
    if (
        not issue_key
        or len(issue_key) > 80
        or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in issue_key
        )
    ):
        raise JiraConfigurationError("Ticket key contains unsupported characters.")
    try:
        async with _http_client(config) as client:
            response = await client.post(
                f"/rest/servicedeskapi/request/{issue_key}/comment",
                json={"body": body, "public": True},
            )
            if response.is_error:
                if response.status_code >= 500:
                    raise JiraOutcomeUnknown(
                        f"Jira returned HTTP {response.status_code}; inspect the ticket before retrying."
                    )
                raise JiraRequestError(
                    f"Jira rejected the comment (HTTP {response.status_code})."
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise JiraOutcomeUnknown(
                    "Jira returned an unreadable success response; inspect the ticket before retrying."
                ) from exc
    except JiraRequestError:
        raise
    except httpx.HTTPError as exc:
        # A timeout can happen after Jira accepted the public comment.
        raise JiraOutcomeUnknown(
            f"Jira comment outcome may be unknown; inspect the ticket before retrying ({type(exc).__name__})."
        ) from exc
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("id"), (str, int))
        or not str(payload.get("id", "")).strip()
        or payload.get("public") is not True
    ):
        raise JiraOutcomeUnknown(
            "Jira did not confirm the comment ID and public visibility; inspect the request before retrying."
        )
    return {"id": str(payload["id"]), "public": "True"}
