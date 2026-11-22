from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlparse

import httpx

from .schemas import CaseRecord


class ITSMConfigurationError(RuntimeError):
    pass


class ITSMRequestError(RuntimeError):
    pass


class ITSMOutcomeUnknown(ITSMRequestError):
    """A provider may have accepted a write without confirming its outcome."""


class JiraConfigurationError(ITSMConfigurationError):
    pass


class JiraRequestError(ITSMRequestError):
    pass


class JiraOutcomeUnknown(JiraRequestError, ITSMOutcomeUnknown):
    """Jira may have accepted a write, so the caller must not retry blindly."""


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str = field(repr=False)
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
            self.auth_configured
            and self.service_desk_id
            and self.queue_id
        )

    @property
    def auth_configured(self) -> bool:
        return bool(self.base_url and self.email and self.api_token)

    def validate(self, require_queue_ids: bool = True) -> None:
        if not self.auth_configured:
            raise JiraConfigurationError("Jira site, account email, and API token are required.")
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
        if require_queue_ids and (
            not self.service_desk_id.isdigit() or not self.queue_id.isdigit()
        ):
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


def _http_client(
    config: JiraConfig, require_queue_ids: bool = True
) -> httpx.AsyncClient:
    config.validate(require_queue_ids=require_queue_ids)
    return httpx.AsyncClient(
        base_url=config.base_url,
        auth=(config.email, config.api_token),
        headers={"Accept": "application/json"},
        timeout=20,
        follow_redirects=False,
    )


def _retry_after_delay(response: object, max_wait: float = 5.0) -> float | None:
    headers = getattr(response, "headers", {})
    retry_after = str(
        headers.get("retry-after", headers.get("Retry-After", ""))
    ).strip()
    if not retry_after:
        return None
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            delay = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if delay < 0 or delay > max_wait:
        return None
    jitter_limit = min(0.25, max_wait - delay)
    return delay + random.uniform(0.0, jitter_limit)


async def _get_with_rate_limit_retry(
    client: httpx.AsyncClient, path: str, params: dict[str, int] | None = None
) -> httpx.Response:
    """Retry idempotent reads at most twice when Jira returns HTTP 429."""
    for attempt in range(3):
        response = (
            await client.get(path)
            if params is None
            else await client.get(path, params=params)
        )
        if getattr(response, "status_code", 200) != 429 or attempt == 2:
            return response
        delay = _retry_after_delay(response)
        if delay is None:
            return response
        await asyncio.sleep(delay)
    raise AssertionError("Unreachable Jira read retry state.")


async def list_service_desks(config: JiraConfig) -> list[dict[str, str]]:
    """List at most 200 service desks the authenticated user can access."""
    results: list[dict[str, str]] = []
    try:
        async with _http_client(config, require_queue_ids=False) as client:
            for start in range(0, 200, 50):
                response = await _get_with_rate_limit_retry(
                    client,
                    "/rest/servicedeskapi/servicedesk",
                    {"start": start, "limit": 50},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("values"), list
                ):
                    raise JiraRequestError(
                        "Jira service desk response has an unsupported shape."
                    )
                values = payload["values"]
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    desk_id = str(item.get("id", "")).strip()
                    if not desk_id.isdigit():
                        continue
                    results.append(
                        {
                            "id": desk_id,
                            "name": str(
                                item.get("projectName") or item.get("projectKey") or desk_id
                            )[:120],
                            "key": str(item.get("projectKey") or "")[:40],
                        }
                    )
                if payload.get("isLastPage") is True or len(values) < 50:
                    break
    except JiraRequestError:
        raise
    except httpx.HTTPStatusError as exc:
        raise JiraRequestError(
            f"Jira service desk discovery failed (HTTP {exc.response.status_code})."
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise JiraRequestError(
            f"Jira service desk discovery failed: {type(exc).__name__}"
        ) from exc
    return results


async def list_service_desk_queues(
    config: JiraConfig, service_desk_id: str
) -> list[dict[str, str]]:
    """List at most 200 queues in one selected service desk; never fetch counts."""
    safe_id = service_desk_id.strip()
    if not safe_id.isdigit():
        raise JiraConfigurationError("Service desk ID must be numeric.")
    results: list[dict[str, str]] = []
    path = f"/rest/servicedeskapi/servicedesk/{safe_id}/queue"
    try:
        async with _http_client(config, require_queue_ids=False) as client:
            for start in range(0, 200, 50):
                response = await _get_with_rate_limit_retry(
                    client, path, {"start": start, "limit": 50}
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("values"), list
                ):
                    raise JiraRequestError(
                        "Jira queue discovery response has an unsupported shape."
                    )
                values = payload["values"]
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    queue_id = str(item.get("id", "")).strip()
                    if not queue_id.isdigit():
                        continue
                    results.append(
                        {
                            "id": queue_id,
                            "name": str(item.get("name") or queue_id)[:120],
                        }
                    )
                if payload.get("isLastPage") is True or len(values) < 50:
                    break
    except JiraConfigurationError:
        raise
    except JiraRequestError:
        raise
    except httpx.HTTPStatusError as exc:
        raise JiraRequestError(
            f"Jira queue discovery failed (HTTP {exc.response.status_code})."
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise JiraRequestError(
            f"Jira queue discovery failed: {type(exc).__name__}"
        ) from exc
    return results


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
            response = await _get_with_rate_limit_retry(
                client, path, {"start": safe_start, "limit": safe_limit}
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise JiraRequestError(
            f"Jira queue read failed (HTTP {exc.response.status_code})."
        ) from exc
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
            response = await _get_with_rate_limit_retry(
                client, f"/rest/servicedeskapi/request/{issue_key}"
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise JiraRequestError(
            f"Jira ticket read failed (HTTP {exc.response.status_code})."
        ) from exc
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


class ITSMAdapter(Protocol):
    """Provider contract consumed by the support workflow.

    Implementations must keep reads bounded, refresh the exact request before a
    write, and return a confirmed public-comment ID. Unknown write outcomes must
    raise :class:`ITSMOutcomeUnknown` so callers never retry a possibly
    accepted write blindly.
    """

    provider_id: str
    capabilities: frozenset[str]

    async def list_workspaces(self) -> list[dict[str, str]]: ...

    async def list_queues(self, workspace_id: str) -> list[dict[str, str]]: ...

    async def validate_connection(self) -> int: ...

    async def list_queue_cases(self, limit: int = 50, start: int = 0) -> list[CaseRecord]: ...

    async def get_request_details(self, issue_key: str) -> dict[str, object]: ...

    async def publish_public_reply(self, issue_key: str, body: str) -> dict[str, str]: ...


@dataclass(frozen=True)
class JiraServiceManagementAdapter:
    """Jira Cloud implementation of the normalized :class:`ITSMAdapter`."""

    config: JiraConfig
    provider_id = "jira-service-management"
    capabilities = frozenset({"queue_read", "request_refresh", "public_reply"})

    async def list_workspaces(self) -> list[dict[str, str]]:
        return await list_service_desks(self.config)

    async def list_queues(self, workspace_id: str) -> list[dict[str, str]]:
        return await list_service_desk_queues(self.config, workspace_id)

    async def validate_connection(self) -> int:
        """Verify credentials and queue access with a single bounded read."""
        return len(await list_queue_cases(self.config, limit=1))

    async def list_queue_cases(self, limit: int = 50, start: int = 0) -> list[CaseRecord]:
        return await list_queue_cases(self.config, limit=limit, start=start)

    async def get_request_details(self, issue_key: str) -> dict[str, object]:
        return await get_request_details(self.config, issue_key)

    async def publish_public_reply(self, issue_key: str, body: str) -> dict[str, str]:
        return await create_public_comment(self.config, issue_key, body)


def create_itsm_adapter(
    provider_id: str, config: JiraConfig | None = None
) -> ITSMAdapter:
    """Construct an implemented provider; unsupported providers fail closed."""
    if provider_id == JiraServiceManagementAdapter.provider_id:
        return JiraServiceManagementAdapter(config or JiraConfig.from_env())
    raise JiraConfigurationError(f"ITSM provider is not implemented: {provider_id}")
