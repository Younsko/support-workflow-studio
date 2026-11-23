from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import load_dotenv, set_key, unset_key
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from .confluence import (
    ConfluenceAdapter,
    ConfluenceConfig,
    ConfluenceConfigurationError,
    ConfluenceRequestError,
)
from .execution_plan import (
    ExecutionPlan,
    ExecutionPlanError,
    compile_published_workflow,
)
from .fixtures import demo_cases
from .itsm import (
    ITSMConfigurationError,
    ITSMOutcomeUnknown,
    ITSMRequestError,
    JiraConfig,
    create_itsm_adapter,
)
from .knowledge import (
    KnowledgeConfigurationError,
    KnowledgeRequestError,
    knowledge_status,
)
from .providers import ProviderConfig, ProviderError, model_destination_scope
from .schemas import (
    CaseRecord,
    DecisionRequest,
    IntakeRequest,
    PublishedWorkflowRunRequest,
    RunRequest,
    StageRecord,
    WorkflowResult,
    WorkspaceMetrics,
)
from .store import WorkflowStateStore, configured_store
from .workflow import content_fingerprint, review_edited_draft, run_workflow
from .workflow_api import configured_workflow_registry
from .workflow_api import router as workflow_definitions_router
from .workflow_definitions import PublishedWorkflow


def _configured_env_path() -> Path:
    default_path = Path(__file__).resolve().parents[2] / ".env"
    return Path(os.getenv("SUPPORT_WORKFLOW_ENV_PATH", str(default_path))).expanduser()


load_dotenv(_configured_env_path())


class SetupRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    jira_base_url: str = Field(default="", max_length=255)
    jira_email: str = Field(default="", max_length=255)
    jira_api_token: str = Field(default="", max_length=2000, repr=False)
    jira_service_desk_id: str = Field(default="", max_length=40)
    jira_queue_id: str = Field(default="", max_length=40)
    knowledge_dir: str = Field(default="", max_length=2048)
    knowledge_provider: str = Field(default="local", max_length=20)
    confluence_base_url: str = Field(default="", max_length=255)
    confluence_email: str = Field(default="", max_length=255)
    confluence_api_token: str = Field(default="", max_length=2000, repr=False)
    confluence_space_keys: str = Field(default="", max_length=640)
    confluence_ticket_search_allowed: bool = False
    auto_triage_new_tickets: bool = False
    poll_interval_seconds: int = Field(default=60, ge=15, le=900)
    max_auto_triage_per_poll: int = Field(default=5, ge=1, le=10)
    n1_keywords: str = Field(default="", max_length=2000)
    n3_plus_keywords: str = Field(default="", max_length=2000)
    n3_keywords: str = Field(default="", max_length=2000)
    model_provider: str | None = Field(default=None, max_length=40)
    model_name: str | None = Field(default=None, max_length=160)
    model_base_url: str | None = Field(default=None, max_length=255)
    model_api_key: str = Field(default="", max_length=2000, repr=False)
    clear_model_api_key: bool = False
    external_model_disclosure_accepted: bool | None = None


class JiraDiscoveryRequest(BaseModel):
    jira_base_url: str = Field(max_length=255)
    jira_email: str = Field(max_length=255)
    jira_api_token: str = Field(default="", max_length=2000, repr=False)
    service_desk_id: str = Field(default="", max_length=40)


class ConfluenceCheckRequest(BaseModel):
    confluence_base_url: str = Field(max_length=255)
    confluence_email: str = Field(max_length=255)
    confluence_api_token: str = Field(default="", max_length=2000, repr=False)
    confluence_space_keys: str = Field(max_length=640)


JIRA_MONITOR_STATE: dict[str, object] = {
    "enabled": False,
    "last_poll_at": None,
    "last_error": None,
    "new_cases_seen": 0,
    "auto_triaged_count": 0,
    "auto_triage_deferred": 0,
    "retry_pending_count": 0,
    "page_cap_reached": False,
    "auto_triage_enabled": False,
}
JIRA_SEEN_KEYS: set[str] = set()
JIRA_PENDING_TRIAGE_ATTEMPTS: dict[str, int] = {}
JIRA_LOCAL_TAGS: dict[str, set[str]] = {}
JIRA_MONITOR_LOCK = asyncio.Lock()
JIRA_MONITOR_BASELINED = False
JIRA_MONITOR_BASELINE_AT: datetime | None = None
JIRA_AUTO_TRIAGE_BASELINE_AT: datetime | None = None
JIRA_AUTO_TRIAGE_WAS_ENABLED = False
WORKFLOW_LOCKS: dict[str, asyncio.Lock] = {}
STATE_STORE: WorkflowStateStore | None = None
SETUP_WRITE_LOCK = asyncio.Lock()


def _persist_local_setup(
    values: dict[str, str],
    api_token: str,
    remove_api_token: bool,
    model_api_key: str = "",
    remove_model_api_key: bool = False,
    confluence_api_token: str = "",
    remove_confluence_api_token: bool = False,
) -> None:
    """Atomically update ignored local configuration, then apply it in-process."""
    env_path = _configured_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=".env.", suffix=".tmp", dir=env_path.parent, delete=False
    ) as handle:
        staged_path = Path(handle.name)
    try:
        if env_path.is_file():
            shutil.copy2(env_path, staged_path)
        for name, value in values.items():
            set_key(str(staged_path), name, value, quote_mode="always")
        if api_token:
            set_key(str(staged_path), "JSM_API_TOKEN", api_token, quote_mode="always")
        elif remove_api_token:
            unset_key(str(staged_path), "JSM_API_TOKEN")
        if model_api_key:
            set_key(str(staged_path), "MODEL_API_KEY", model_api_key, quote_mode="always")
        elif remove_model_api_key:
            unset_key(str(staged_path), "MODEL_API_KEY")
            unset_key(str(staged_path), "MODEL_API_KEY_SCOPE")
        if confluence_api_token:
            set_key(str(staged_path), "CONFLUENCE_API_TOKEN", confluence_api_token, quote_mode="always")
        elif remove_confluence_api_token:
            unset_key(str(staged_path), "CONFLUENCE_API_TOKEN")
        if os.name != "nt":
            staged_path.chmod(0o600)
        os.replace(staged_path, env_path)
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise

    os.environ.update(values)
    if api_token:
        os.environ["JSM_API_TOKEN"] = api_token
    elif remove_api_token:
        os.environ.pop("JSM_API_TOKEN", None)
    if model_api_key:
        os.environ["MODEL_API_KEY"] = model_api_key
    elif remove_model_api_key:
        os.environ.pop("MODEL_API_KEY", None)
        os.environ.pop("MODEL_API_KEY_SCOPE", None)
    if confluence_api_token:
        os.environ["CONFLUENCE_API_TOKEN"] = confluence_api_token
    elif remove_confluence_api_token:
        os.environ.pop("CONFLUENCE_API_TOKEN", None)


def _save_case(case: CaseRecord) -> None:
    if STATE_STORE is not None:
        STATE_STORE.save_case(case)


def _save_run(
    run: WorkflowResult,
    *,
    audit_event: tuple[str, str, str, dict[str, object]] | None = None,
    execution_claim: tuple[str, str] | None = None,
) -> None:
    if STATE_STORE is not None:
        STATE_STORE.save_run(run, audit_event=audit_event, execution_claim=execution_claim)


def _record_published_execution_stage(
    store: WorkflowStateStore,
    idempotency_key: str,
    lease_owner: str,
    run_id: str,
    attempt: int,
    stage: StageRecord,
    ordinal: int,
) -> None:
    now = datetime.now(UTC)
    if not store.renew_published_run_claim(idempotency_key, lease_owner, now):
        raise RuntimeError("Published workflow execution lease expired or was transferred.")
    store.record_execution_step(
        run_id=run_id,
        attempt=attempt,
        ordinal=ordinal,
        stage_id=stage.id,
        status=stage.status,
        elapsed_ms=stage.elapsed_ms,
        recorded_at=now,
    )


def _enabled_env(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes"}


def _monitor_checkpoint() -> dict[str, object]:
    return {
        "seen_keys": sorted(JIRA_SEEN_KEYS),
        "pending_attempts": dict(JIRA_PENDING_TRIAGE_ATTEMPTS),
        "local_tags": {case_id: sorted(tags) for case_id, tags in JIRA_LOCAL_TAGS.items()},
        "monitor_baselined": JIRA_MONITOR_BASELINED,
        "monitor_baseline_at": JIRA_MONITOR_BASELINE_AT.isoformat() if JIRA_MONITOR_BASELINE_AT else None,
        "auto_triage_baseline_at": JIRA_AUTO_TRIAGE_BASELINE_AT.isoformat() if JIRA_AUTO_TRIAGE_BASELINE_AT else None,
        "auto_triage_was_enabled": JIRA_AUTO_TRIAGE_WAS_ENABLED,
    }


def _save_monitor_checkpoint() -> None:
    if STATE_STORE is not None:
        STATE_STORE.save_monitor_state(_monitor_checkpoint())


def _restore_monitor_checkpoint(state: dict[str, object]) -> None:
    global JIRA_MONITOR_BASELINED, JIRA_MONITOR_BASELINE_AT
    global JIRA_AUTO_TRIAGE_BASELINE_AT, JIRA_AUTO_TRIAGE_WAS_ENABLED
    seen = state.get("seen_keys")
    if isinstance(seen, list):
        JIRA_SEEN_KEYS.update(value for value in seen if isinstance(value, str))
    attempts = state.get("pending_attempts")
    if isinstance(attempts, dict):
        for case_id, count in attempts.items():
            if isinstance(case_id, str) and isinstance(count, int) and 0 <= count <= 3:
                JIRA_PENDING_TRIAGE_ATTEMPTS[case_id] = count
    tags = state.get("local_tags")
    if isinstance(tags, dict):
        for case_id, values in tags.items():
            if isinstance(case_id, str) and isinstance(values, list):
                JIRA_LOCAL_TAGS[case_id] = {
                    tag for tag in values if isinstance(tag, str) and tag.startswith("studio-triage-")
                }
    for case_id, case in CASES.items():
        JIRA_LOCAL_TAGS.setdefault(
            case_id, {tag for tag in case.tags if tag.startswith("studio-triage-")}
        )
    JIRA_MONITOR_BASELINED = state.get("monitor_baselined") is True
    for name, global_name in (
        ("monitor_baseline_at", "monitor"),
        ("auto_triage_baseline_at", "auto"),
    ):
        raw = state.get(name)
        try:
            value = datetime.fromisoformat(raw) if isinstance(raw, str) else None
        except ValueError:
            value = None
        if global_name == "monitor":
            JIRA_MONITOR_BASELINE_AT = value
        else:
            JIRA_AUTO_TRIAGE_BASELINE_AT = value
    JIRA_AUTO_TRIAGE_WAS_ENABLED = state.get("auto_triage_was_enabled") is True


def _private_local_mode_enabled() -> bool:
    return _enabled_env("PRIVATE_LOCAL_MODE")


def _validate_private_local_mode() -> None:
    if not _private_local_mode_enabled():
        return
    provider = ProviderConfig.from_env()
    if provider.provider != "ollama-local" or not provider.live:
        raise RuntimeError(
            "PRIVATE_LOCAL_MODE requires MODEL_PROVIDER=ollama-local, a loopback endpoint, and a model name."
        )
    if any(
        os.getenv(name, "").strip()
        for name in (
            "JSM_BASE_URL",
            "JSM_USER_EMAIL",
            "JSM_API_TOKEN",
            "JSM_SERVICE_DESK_ID",
            "JSM_QUEUE_ID",
        )
    ):
        raise RuntimeError(
            "PRIVATE_LOCAL_MODE refuses Jira connector configuration; remove all JSM settings."
        )
    if (
        _enabled_env("ALLOW_EXTERNAL_TICKET_INFERENCE")
        or _enabled_env("JSM_AUTO_TRIAGE_NEW_TICKETS")
        or any(
            os.getenv(name, "").strip()
            for name in (
                "COUNCIL_PROVIDER",
                "COUNCIL_MODEL",
                "COUNCIL_API_KEY",
                "COUNCIL_BASE_URL",
                "JUDGE_PROVIDER",
                "JUDGE_MODEL",
                "JUDGE_API_KEY",
                "JUDGE_BASE_URL",
            )
        )
    ):
        raise RuntimeError(
            "PRIVATE_LOCAL_MODE refuses external inference consent and council/judge profiles."
        )


def _workflow_lock(case_id: str) -> asyncio.Lock:
    return WORKFLOW_LOCKS.setdefault(case_id, asyncio.Lock())


def _tag_local_case(case_id: str, tag: str, enabled: bool) -> None:
    tags = JIRA_LOCAL_TAGS.setdefault(case_id, set())
    if enabled:
        tags.add(tag)
    else:
        tags.discard(tag)
    if not tags:
        JIRA_LOCAL_TAGS.pop(case_id, None)
    case = CASES.get(case_id)
    if case is not None:
        preserved = [item for item in case.tags if item.startswith("studio-triage-")]
        case_tags = set(case.tags) - set(preserved)
        case_tags.update(JIRA_LOCAL_TAGS.get(case_id, set()))
        CASES[case_id] = case.model_copy(update={"tags": sorted(case_tags)})
        _save_case(CASES[case_id])


def _monitor_interval_seconds() -> int:
    try:
        configured = int(os.getenv("JSM_POLL_INTERVAL_SECONDS", "60"))
    except ValueError:
        configured = 60
    return max(15, min(900, configured))


def _auto_triage_limit() -> int:
    try:
        configured = int(os.getenv("JSM_AUTO_TRIAGE_MAX_PER_POLL", "5"))
    except ValueError:
        configured = 5
    return max(1, min(10, configured))


def _created_after_baseline(created_at: str, baseline: datetime | None) -> bool:
    if baseline is None:
        return False
    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if created.tzinfo is None:
        return False
    return created.astimezone(UTC) > baseline


async def _poll_jira_once() -> None:
    global JIRA_MONITOR_BASELINED, JIRA_MONITOR_BASELINE_AT
    global JIRA_AUTO_TRIAGE_BASELINE_AT, JIRA_AUTO_TRIAGE_WAS_ENABLED
    if _private_local_mode_enabled():
        JIRA_MONITOR_STATE.update(enabled=False, last_error=None)
        return
    config = JiraConfig.from_env()
    if not config.configured:
        JIRA_MONITOR_STATE.update(enabled=False, last_error=None)
        return
    adapter = create_itsm_adapter("jira-service-management", config)

    auto_triage = _enabled_env("JSM_AUTO_TRIAGE_NEW_TICKETS")
    async with JIRA_MONITOR_LOCK:
        poll_started_at = datetime.now(UTC)
        if auto_triage and not JIRA_AUTO_TRIAGE_WAS_ENABLED:
            JIRA_AUTO_TRIAGE_BASELINE_AT = poll_started_at
        elif not auto_triage:
            JIRA_AUTO_TRIAGE_BASELINE_AT = None
        JIRA_AUTO_TRIAGE_WAS_ENABLED = auto_triage
        remote_cases: list[CaseRecord] = []
        page_size = 50
        maximum = 200
        page_cap_reached = False
        for start in range(0, maximum + page_size, page_size):
            page = await adapter.list_queue_cases(limit=page_size, start=start)
            if start == maximum:
                page_cap_reached = bool(page)
                break
            remote_cases.extend(page)
            if len(page) < page_size:
                break
        unique_cases = {case.id: case for case in remote_cases}
        remote_cases = list(unique_cases.values())
        now = datetime.now(UTC).isoformat(timespec="seconds")
        new_cases = [case for case in remote_cases if case.id not in JIRA_SEEN_KEYS]
        for case in remote_cases:
            CASES[case.id] = case.model_copy(
                update={
                    "tags": sorted(set(case.tags) | JIRA_LOCAL_TAGS.get(case.id, set()))
                }
            )
            _save_case(CASES[case.id])

        JIRA_SEEN_KEYS.update(case.id for case in remote_cases)
        # The first successful poll establishes a creation-time baseline. The
        # auto-triage baseline also resets when its opt-in setting is enabled.
        if not JIRA_MONITOR_BASELINED:
            JIRA_MONITOR_BASELINED = True
            JIRA_MONITOR_BASELINE_AT = poll_started_at
        new_cases_after_monitor_baseline = [
            case
            for case in new_cases
            if _created_after_baseline(case.created_at, JIRA_MONITOR_BASELINE_AT)
        ]
        new_cases_seen = len(new_cases_after_monitor_baseline)
        new_cases = [
            case
            for case in new_cases_after_monitor_baseline
            if auto_triage
            and _created_after_baseline(case.created_at, JIRA_AUTO_TRIAGE_BASELINE_AT)
        ]

        current_ids = {case.id for case in remote_cases}
        for case_id in list(JIRA_PENDING_TRIAGE_ATTEMPTS):
            if case_id not in current_ids and not page_cap_reached:
                JIRA_PENDING_TRIAGE_ATTEMPTS.pop(case_id, None)
                _tag_local_case(case_id, "studio-triage-retry-pending", False)
        if auto_triage:
            for case in new_cases:
                JIRA_PENDING_TRIAGE_ATTEMPTS.setdefault(case.id, 0)
        pending_cases = [
            case
            for case in remote_cases
            if case.id in JIRA_PENDING_TRIAGE_ATTEMPTS
        ]
        JIRA_MONITOR_STATE.update(
            enabled=True,
            last_poll_at=now,
            last_error=None,
            new_cases_seen=new_cases_seen,
            auto_triaged_count=0,
            auto_triage_deferred=0,
            retry_pending_count=len(JIRA_PENDING_TRIAGE_ATTEMPTS),
            page_cap_reached=page_cap_reached,
            auto_triage_enabled=auto_triage,
        )
        _save_monitor_checkpoint()

    if not auto_triage or not pending_cases:
        return

    knowledge = knowledge_status()
    provider = ProviderConfig.from_env()
    if (
        knowledge["mode"] != "local"
        or not knowledge["configured"]
        or not knowledge["available"]
    ):
        JIRA_MONITOR_STATE["last_error"] = (
            "Auto-triage paused: operator knowledge is not configured."
        )
        for case in pending_cases:
            _tag_local_case(case.id, "studio-triage-paused-knowledge", True)
        _save_monitor_checkpoint()
        return
    if (
        provider.requires_external_ticket_consent
        and not provider.external_ticket_inference_allowed
    ):
        JIRA_MONITOR_STATE["last_error"] = (
            "Auto-triage paused: external ticket inference is disabled."
        )
        for case in pending_cases:
            _tag_local_case(case.id, "studio-triage-paused-consent", True)
        _save_monitor_checkpoint()
        return
    if provider.requires_external_ticket_consent and not provider.live:
        JIRA_MONITOR_STATE["last_error"] = (
            "Auto-triage paused: selected external model credentials are not configured "
            "for the current provider destination."
        )
        for case in pending_cases:
            _tag_local_case(case.id, "studio-triage-paused-model-setup", True)
        _save_monitor_checkpoint()
        return

    ordered_pending_cases = sorted(
        pending_cases, key=lambda item: item.created_at, reverse=True
    )
    limit = _auto_triage_limit()
    selected_for_auto_triage = ordered_pending_cases[:limit]
    JIRA_MONITOR_STATE["auto_triage_deferred"] = max(
        0, len(ordered_pending_cases) - len(selected_for_auto_triage)
    )
    for case in selected_for_auto_triage:
        async with _workflow_lock(case.id):
            if any(run.case_id == case.id for run in RUNS.values()):
                JIRA_PENDING_TRIAGE_ATTEMPTS.pop(case.id, None)
                _tag_local_case(case.id, "studio-triage-retry-pending", False)
                _save_monitor_checkpoint()
                continue
            attempts = JIRA_PENDING_TRIAGE_ATTEMPTS.get(case.id, 0)
            if attempts >= 3:
                JIRA_MONITOR_STATE["last_error"] = (
                    "Auto-triage stopped after three interrupted or failed attempts."
                )
                JIRA_PENDING_TRIAGE_ATTEMPTS.pop(case.id, None)
                _tag_local_case(case.id, "studio-triage-retry-pending", False)
                _tag_local_case(case.id, "studio-triage-failed", True)
                _save_monitor_checkpoint()
                continue
            # Reserve the attempt before any provider work. A process restart during
            # generation therefore cannot silently grant an unbounded retry.
            attempts += 1
            JIRA_PENDING_TRIAGE_ATTEMPTS[case.id] = attempts
            _save_monitor_checkpoint()
            try:
                details = await adapter.get_request_details(case.external_key or case.id)
                if str(details.get("status_category", "")).upper() == "DONE":
                    CASES[case.id] = case.model_copy(
                        update={
                            "title": details["summary"],
                            "body": details["description"],
                            "status": str(details.get("status") or case.status),
                        }
                    )
                    _save_case(CASES[case.id])
                    JIRA_PENDING_TRIAGE_ATTEMPTS.pop(case.id, None)
                    for tag in (
                        "studio-triage-paused-knowledge",
                        "studio-triage-paused-consent",
                        "studio-triage-failed",
                        "studio-triage-retry-pending",
                    ):
                        _tag_local_case(case.id, tag, False)
                    _save_monitor_checkpoint()
                    continue
                case = case.model_copy(
                    update={
                        "title": details["summary"],
                        "body": details["description"],
                        "status": str(details.get("status") or case.status),
                    }
                )
                CASES[case.id] = case
                _save_case(case)
                result = await run_workflow(
                    case,
                    provider,
                    source_snapshot_hash=_source_snapshot_hash(details, case),
                )
            except (
                ITSMConfigurationError,
                ITSMRequestError,
                ProviderError,
                RuntimeError,
            ) as exc:
                JIRA_MONITOR_STATE["last_error"] = (
                    f"Auto-triage attempt {attempts}/3 failed ({type(exc).__name__})."
                )
                if attempts >= 3:
                    JIRA_PENDING_TRIAGE_ATTEMPTS.pop(case.id, None)
                    _tag_local_case(case.id, "studio-triage-retry-pending", False)
                    _tag_local_case(case.id, "studio-triage-failed", True)
                else:
                    JIRA_PENDING_TRIAGE_ATTEMPTS[case.id] = attempts
                    _tag_local_case(case.id, "studio-triage-failed", False)
                    _tag_local_case(case.id, "studio-triage-retry-pending", True)
                _save_monitor_checkpoint()
                continue
            RUNS[result.id] = result
            _save_run(result)
            JIRA_PENDING_TRIAGE_ATTEMPTS.pop(case.id, None)
            for tag in (
                "studio-triage-paused-knowledge",
                "studio-triage-paused-consent",
                "studio-triage-failed",
                "studio-triage-retry-pending",
            ):
                _tag_local_case(case.id, tag, False)
            _save_monitor_checkpoint()
            JIRA_MONITOR_STATE["auto_triaged_count"] = (
                int(JIRA_MONITOR_STATE["auto_triaged_count"]) + 1
            )
    JIRA_MONITOR_STATE["retry_pending_count"] = len(
        JIRA_PENDING_TRIAGE_ATTEMPTS
    )
    _save_monitor_checkpoint()


async def _jira_monitor_loop() -> None:
    while True:
        try:
            await _poll_jira_once()
        except Exception as exc:  # noqa: BLE001 - keep the monitor alive per cycle
            JIRA_MONITOR_STATE.update(
                enabled=JiraConfig.from_env().configured,
                last_error=f"Jira monitor cycle failed ({type(exc).__name__}).",
            )
        await asyncio.sleep(_monitor_interval_seconds())


@asynccontextmanager
async def lifespan(_: FastAPI):
    global STATE_STORE
    _validate_private_local_mode()
    STATE_STORE = configured_store()
    CASES.update(STATE_STORE.load_cases())
    RUNS.update(STATE_STORE.load_runs())
    _restore_monitor_checkpoint(STATE_STORE.load_monitor_state())
    monitor = asyncio.create_task(_jira_monitor_loop())
    try:
        yield
    finally:
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass
        STATE_STORE = None


app = FastAPI(title="Support Workflow Studio", version="0.1.0", lifespan=lifespan)
app.include_router(workflow_definitions_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    """Describe invalid fields without echoing submitted values such as tokens."""
    messages: list[str] = []
    reasons = {
        "missing": "is required",
        "string_too_long": "is too long",
        "string_too_short": "is too short",
        "int_parsing": "must be an integer",
        "greater_than_equal": "is below its minimum",
        "less_than_equal": "is above its maximum",
    }
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part != "body"]
        field = ".".join(location) or "request"
        reason = reasons.get(str(error.get("type", "")), "is invalid")
        messages.append(f"{field} {reason}")
    detail = "; ".join(messages) or "Request validation failed."
    return JSONResponse(status_code=422, content={"detail": detail})


@app.middleware("http")
async def require_local_client(request: Request, call_next) -> Response:
    """This unauthenticated prototype must only accept loopback connections."""
    host = request.client.host if request.client else ""
    if host == "testclient":
        return await call_next(request)
    allowed_hostnames = {"localhost", "127.0.0.1", "::1"}
    if request.url.hostname not in allowed_hostnames:
        return JSONResponse(
            status_code=403,
            content={"detail": "This prototype only accepts loopback hostnames."},
        )
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
        parsed_origin = urlsplit(origin)
        normalized_origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}".casefold()
        same_origin = normalized_origin == (
            f"{request.url.scheme}://{request.url.netloc}"
        ).casefold()
        vite_dev_origin = normalized_origin in {
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
        valid_origin = (
            parsed_origin.scheme == "http"
            and parsed_origin.hostname in allowed_hostnames
            and not parsed_origin.username
            and not parsed_origin.password
            and parsed_origin.path in {"", "/"}
            and not parsed_origin.query
            and not parsed_origin.fragment
        )
        if not valid_origin or not (same_origin or vite_dev_origin):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-origin local actions are not allowed."},
            )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None or not address.is_loopback:
        return JSONResponse(
            status_code=403,
            content={"detail": "This prototype only accepts local connections."},
        )
    return await call_next(request)


CASES: dict[str, CaseRecord] = {case.id: case for case in demo_cases()}
RUNS: dict[str, WorkflowResult] = {}
PUBLISH_LOCKS: dict[str, asyncio.Lock] = {}
DECISION_LOCKS: dict[str, asyncio.Lock] = {}


@app.get("/api/health")
async def health() -> dict[str, str | bool]:
    config = ProviderConfig.from_env()
    return {
        "status": "ok",
        "provider_mode": "live" if config.live else "demo",
        "provider": config.provider,
        "model": config.model,
        "credentials_configured": config.live,
        "private_local_mode": _private_local_mode_enabled(),
    }


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    """Process liveness probe; it deliberately does not depend on connectors."""
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness() -> dict[str, str]:
    """Readiness probe for local execution; never reports provider credentials."""
    store = STATE_STORE
    if store is None:
        raise HTTPException(status_code=503, detail="Local state storage is not ready.")
    try:
        store.check_ready()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=503, detail="Local state storage is not ready.") from exc
    return {"status": "ready", "storage": "available"}


@app.get("/api/setup")
async def get_setup() -> dict[str, object]:
    """Return editable setup state without ever returning the stored API token."""
    jira = JiraConfig.from_env()
    model = ProviderConfig.from_env()
    confluence_base_url = os.getenv("CONFLUENCE_BASE_URL", "")
    confluence_email = os.getenv("CONFLUENCE_USER_EMAIL", "")
    confluence_spaces = os.getenv("CONFLUENCE_SPACE_KEYS", "")
    confluence_token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
    confluence_token_configured = False
    try:
        confluence_setup = ConfluenceConfig(
            base_url=confluence_base_url,
            email=confluence_email,
            api_token=confluence_token or "configured-placeholder",
            space_keys=tuple(value.strip() for value in confluence_spaces.split(",") if value.strip()),
        )
        confluence_setup.validate()
        confluence_scope = confluence_setup.credential_scope
        confluence_consent_scope = confluence_setup.consent_scope
        confluence_token_configured = bool(confluence_token) and (
            os.getenv("CONFLUENCE_API_TOKEN_SCOPE", "").strip() == confluence_scope
        )
    except ConfluenceConfigurationError:
        confluence_scope = ""
        confluence_consent_scope = ""
    return {
        "jira_base_url": jira.base_url,
        "jira_email": jira.email,
        "jira_service_desk_id": jira.service_desk_id,
        "jira_queue_id": jira.queue_id,
        "jira_token_configured": bool(jira.api_token),
        "knowledge_dir": os.getenv("KNOWLEDGE_DIR", ""),
        "knowledge_provider": os.getenv("KNOWLEDGE_PROVIDER", "local"),
        "confluence_base_url": confluence_base_url,
        "confluence_email": confluence_email,
        "confluence_space_keys": confluence_spaces,
        "confluence_api_token_configured": confluence_token_configured,
        "confluence_ticket_search_allowed": (
            _enabled_env("CONFLUENCE_TICKET_SEARCH_ALLOWED")
            and bool(confluence_consent_scope)
            and os.getenv("CONFLUENCE_SEARCH_CONSENT_SCOPE", "").strip() == confluence_consent_scope
        ),
        "auto_triage_new_tickets": _enabled_env("JSM_AUTO_TRIAGE_NEW_TICKETS"),
        "poll_interval_seconds": _monitor_interval_seconds(),
        "max_auto_triage_per_poll": _auto_triage_limit(),
        "n1_keywords": os.getenv("SUPPORT_N1_KEYWORDS", ""),
        "n3_plus_keywords": os.getenv("SUPPORT_N3_PLUS_KEYWORDS", ""),
        "n3_keywords": os.getenv("SUPPORT_N3_KEYWORDS", ""),
        "model_provider": model.provider,
        "model_name": model.model,
        "model_base_url": model.base_url,
        "model_api_key_configured": bool(model.api_key),
        "external_model_disclosure_accepted": model.external_ticket_inference_allowed,
        "private_local_mode": _private_local_mode_enabled(),
    }


def _validated_model_setup(settings: SetupRequest) -> tuple[dict[str, str], str, bool]:
    """Validate local model settings without sending a prompt or probing a provider."""
    current = ProviderConfig.from_env()
    provider = (settings.model_provider or current.provider).strip().lower()
    model_name = (settings.model_name if settings.model_name is not None else current.model).strip()
    base_url = (
        settings.model_base_url
        if settings.model_base_url is not None
        else current.base_url
    ).strip().rstrip("/")
    api_key = settings.model_api_key.strip()
    target_scope = model_destination_scope(provider, base_url)
    current_scope = model_destination_scope(current.provider, current.base_url)
    saved_key = os.getenv("MODEL_API_KEY", "").strip()
    saved_key_scope = os.getenv("MODEL_API_KEY_SCOPE", "").strip()
    saved_consent_scope = os.getenv("MODEL_CONSENT_SCOPE", "").strip()
    previously_consented_here = (
        current.external_ticket_inference_allowed
        and saved_consent_scope == target_scope
    )
    consent = (
        settings.external_model_disclosure_accepted
        if settings.external_model_disclosure_accepted is not None
        else previously_consented_here
    )
    model_fields_submitted = any(
        value is not None
        for value in (
            settings.model_provider,
            settings.model_name,
            settings.model_base_url,
            settings.external_model_disclosure_accepted,
        )
    )
    if not model_fields_submitted and not api_key and not settings.clear_model_api_key:
        return {}, "", False

    supported = {"demo", "openai-compatible", "anthropic", "ollama-local"}
    if provider not in supported:
        raise HTTPException(status_code=422, detail="Choose demo, OpenAI-compatible, Anthropic, or local Ollama.")
    if provider != "demo" and not model_name:
        raise HTTPException(status_code=422, detail="A model name is required for the selected provider.")
    if settings.clear_model_api_key and api_key:
        raise HTTPException(status_code=422, detail="Choose either a replacement model key or remove the saved key.")

    if settings.clear_model_api_key:
        effective_key = ""
    elif api_key:
        effective_key = api_key
    elif saved_key and target_scope == saved_key_scope:
        effective_key = saved_key
    elif not saved_key and target_scope == current_scope and current.api_key:
        # Supports an operator-provided GROQ_API_KEY at its matching endpoint only.
        effective_key = current.api_key
    else:
        effective_key = ""
    if provider in {"openai-compatible", "anthropic"}:
        if not effective_key:
            raise HTTPException(status_code=422, detail="Enter a model API key before selecting this provider.")
        if not consent:
            raise HTTPException(
                status_code=422,
                detail="Confirm that the selected external model provider will receive ticket content before saving.",
            )
    if _private_local_mode_enabled() and provider != "ollama-local":
        raise HTTPException(status_code=403, detail="Private local mode only allows local Ollama models.")

    if provider == "openai-compatible":
        parsed = urlsplit(base_url)
        host = (parsed.hostname or "").casefold()
        try:
            try:
                address = ipaddress.ip_address(host) if host else None
            except ValueError:
                address = None
            port = parsed.port
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Model endpoint URL is invalid.") from exc
        loopback = (address is not None and address.is_loopback) or host == "localhost"
        if (
            not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
            or (parsed.scheme == "https" and port not in (None, 443))
        ):
            raise HTTPException(
                status_code=422,
                detail="Model endpoint must use HTTPS, or HTTP on a local loopback host, without credentials, query, or fragment.",
            )
    elif provider == "ollama-local":
        parsed = urlsplit(base_url)
        host = (parsed.hostname or "").casefold()
        try:
            try:
                address = ipaddress.ip_address(host) if host else None
            except ValueError:
                address = None
            port = parsed.port
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Local Ollama endpoint URL is invalid.") from exc
        loopback = (address is not None and address.is_loopback) or host == "localhost"
        if (
            parsed.scheme != "http"
            or not loopback
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or port not in (None, 80, 11434)
        ):
            raise HTTPException(
                status_code=422,
                detail="Local Ollama must use an HTTP loopback URL on port 80 or 11434 without a path or credentials.",
            )

    values = {
        "MODEL_PROVIDER": provider,
        "MODEL_NAME": model_name or "Synthetic workflow model",
        "MODEL_BASE_URL": base_url,
        "ALLOW_EXTERNAL_TICKET_INFERENCE": str(bool(consent)).lower(),
        "MODEL_CONSENT_SCOPE": target_scope,
    }
    if api_key or (saved_key and target_scope == saved_key_scope and not settings.clear_model_api_key):
        values["MODEL_API_KEY_SCOPE"] = target_scope
    return values, api_key, settings.clear_model_api_key


def _jira_discovery_config(settings: JiraDiscoveryRequest) -> JiraConfig:
    if _private_local_mode_enabled():
        raise HTTPException(
            status_code=403,
            detail="Jira discovery is disabled while PRIVATE_LOCAL_MODE is active.",
        )
    if any(
        ord(character) < 32 or ord(character) == 127
        for value in (settings.jira_base_url, settings.jira_email, settings.jira_api_token, settings.service_desk_id)
        for character in value
    ):
        raise HTTPException(status_code=422, detail="Jira setup fields contain unsupported control characters.")
    api_token = settings.jira_api_token.strip() or os.getenv("JSM_API_TOKEN", "").strip()
    config = JiraConfig(
        settings.jira_base_url.strip().rstrip("/"),
        settings.jira_email.strip(),
        api_token,
        settings.service_desk_id.strip(),
        "",
    )
    try:
        config.validate(require_queue_ids=False)
    except ITSMConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return config


def _confluence_setup_config(
    base_url: str, email: str, api_token: str, space_keys: str
) -> ConfluenceConfig:
    spaces = tuple(value.strip().upper() for value in space_keys.split(",") if value.strip())
    candidate = ConfluenceConfig(
        base_url=base_url.strip().rstrip("/"),
        email=email.strip(),
        api_token=api_token.strip() or "configured-placeholder",
        space_keys=spaces,
    )
    candidate.validate()
    if not api_token.strip():
        saved_token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
        saved_scope = os.getenv("CONFLUENCE_API_TOKEN_SCOPE", "").strip()
        if not saved_token or saved_scope != candidate.credential_scope:
            raise HTTPException(
                status_code=422,
                detail="Enter a Confluence API token for this site, account, and space selection.",
            )
        candidate = ConfluenceConfig(
            base_url=candidate.base_url,
            email=candidate.email,
            api_token=saved_token,
            space_keys=candidate.space_keys,
        )
    return candidate


@app.post("/api/setup/confluence/check")
async def check_confluence_connection(settings: ConfluenceCheckRequest) -> dict[str, object]:
    """Check Confluence reachability only after the operator clicks the check button."""
    if _private_local_mode_enabled():
        raise HTTPException(
            status_code=403,
            detail="Confluence connections are disabled while PRIVATE_LOCAL_MODE is active.",
        )
    if any(
        ord(character) < 32 or ord(character) == 127
        for value in (settings.confluence_base_url, settings.confluence_email,
                      settings.confluence_api_token, settings.confluence_space_keys)
        for character in value
    ):
        raise HTTPException(status_code=422, detail="Confluence setup fields contain unsupported control characters.")
    try:
        config = _confluence_setup_config(
            settings.confluence_base_url,
            settings.confluence_email,
            settings.confluence_api_token,
            settings.confluence_space_keys,
        )
        return await ConfluenceAdapter(config).check_connection()
    except ConfluenceConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConfluenceRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/setup/jira/workspaces")
async def discover_jira_workspaces(settings: JiraDiscoveryRequest) -> list[dict[str, str]]:
    """Discover accessible service desks without persisting submitted credentials."""
    config = _jira_discovery_config(settings)
    try:
        return await create_itsm_adapter("jira-service-management", config).list_workspaces()
    except ITSMConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ITSMRequestError as exc:
        raise HTTPException(status_code=502, detail=f"Jira service desk lookup failed: {exc}") from exc


@app.post("/api/setup/jira/queues")
async def discover_jira_queues(settings: JiraDiscoveryRequest) -> list[dict[str, str]]:
    """Discover queues from one selected service desk without saving settings."""
    config = _jira_discovery_config(settings)
    try:
        return await create_itsm_adapter("jira-service-management", config).list_queues(settings.service_desk_id)
    except ITSMConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ITSMRequestError as exc:
        raise HTTPException(status_code=502, detail=f"Jira queue lookup failed: {exc}") from exc


@app.put("/api/setup")
async def save_setup(settings: SetupRequest) -> dict[str, object]:
    """Verify an optional Jira connection, then save local operator settings."""
    model_env_values, model_api_key, remove_model_api_key = _validated_model_setup(settings)
    knowledge_provider = settings.knowledge_provider.strip().lower()
    if knowledge_provider not in {"local", "confluence"}:
        raise HTTPException(status_code=422, detail="Choose local files or Confluence Cloud as the knowledge source.")
    connection_values = (
        settings.jira_base_url,
        settings.jira_email,
        settings.jira_api_token,
        settings.jira_service_desk_id,
        settings.jira_queue_id,
        settings.knowledge_dir,
        settings.confluence_base_url,
        settings.confluence_email,
        settings.confluence_api_token,
        settings.confluence_space_keys,
        settings.model_provider or "",
        settings.model_name or "",
        settings.model_base_url or "",
        settings.model_api_key,
    )
    if any(
        ord(character) < 32 or ord(character) == 127
        for value in connection_values
        for character in value
    ) or any(
        (ord(character) < 32 and character != "\n") or ord(character) == 127
        for value in (settings.n1_keywords, settings.n3_plus_keywords, settings.n3_keywords)
        for character in value
    ):
        raise HTTPException(status_code=422, detail="Setup fields contain unsupported control characters.")
    if _private_local_mode_enabled() and (
        any((settings.jira_base_url, settings.jira_email, settings.jira_api_token))
        or knowledge_provider == "confluence"
    ):
        raise HTTPException(status_code=403, detail="External connections are disabled while PRIVATE_LOCAL_MODE is active.")

    url = settings.jira_base_url.strip().rstrip("/")
    email = settings.jira_email.strip()
    desk = settings.jira_service_desk_id.strip()
    queue = settings.jira_queue_id.strip()
    token = settings.jira_api_token.strip() or os.getenv("JSM_API_TOKEN", "").strip()
    jira_fields = (url, email, desk, queue)
    queue_sample_count = 0
    if any(jira_fields) or settings.jira_api_token.strip():
        if not all(jira_fields) or not token:
            raise HTTPException(status_code=422, detail="Enter the Jira site, account email, service desk ID, queue ID, and API token (or keep the saved token).")
        candidate = JiraConfig(url, email, token, desk, queue)
        try:
            queue_sample_count = await create_itsm_adapter("jira-service-management", candidate).validate_connection()
        except ITSMConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ITSMRequestError as exc:
            raise HTTPException(status_code=502, detail=f"Jira connection check failed: {exc}") from exc

    knowledge_dir = settings.knowledge_dir.strip() if knowledge_provider == "local" else ""
    if knowledge_provider == "local" and knowledge_dir:
        candidate_path = Path(knowledge_dir).expanduser()
        if not candidate_path.is_dir():
            raise HTTPException(status_code=422, detail="Knowledge directory does not exist on this machine.")
        previous_knowledge_dir = os.environ.get("KNOWLEDGE_DIR")
        os.environ["KNOWLEDGE_DIR"] = str(candidate_path.resolve())
        try:
            knowledge_check = knowledge_status()
        finally:
            if previous_knowledge_dir is None:
                os.environ.pop("KNOWLEDGE_DIR", None)
            else:
                os.environ["KNOWLEDGE_DIR"] = previous_knowledge_dir
        if not knowledge_check.get("available"):
            raise HTTPException(status_code=422, detail=str(knowledge_check.get("message") or "Knowledge source allowlist could not be verified."))

    confluence_config: ConfluenceConfig | None = None
    confluence_token = ""
    confluence_consent = False
    if knowledge_provider == "confluence":
        try:
            confluence_config = _confluence_setup_config(
                settings.confluence_base_url,
                settings.confluence_email,
                settings.confluence_api_token,
                settings.confluence_space_keys,
            )
        except ConfluenceConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        new_scope = confluence_config.consent_scope
        prior_consent = (
            _enabled_env("CONFLUENCE_TICKET_SEARCH_ALLOWED")
            and os.getenv("CONFLUENCE_SEARCH_CONSENT_SCOPE", "").strip() == new_scope
        )
        confluence_consent = settings.confluence_ticket_search_allowed or prior_consent
        if not confluence_consent:
            raise HTTPException(
                status_code=422,
                detail="Confirm that minimized ticket search terms will be sent to the selected Confluence site and spaces before saving.",
            )
        confluence_token = settings.confluence_api_token.strip()

    env_values = {
        "JSM_BASE_URL": url,
        "JSM_USER_EMAIL": email,
        "JSM_SERVICE_DESK_ID": desk,
        "JSM_QUEUE_ID": queue,
        "KNOWLEDGE_DIR": str(Path(knowledge_dir).expanduser().resolve()) if knowledge_dir else "",
        "KNOWLEDGE_PROVIDER": knowledge_provider,
        "CONFLUENCE_BASE_URL": confluence_config.normalized_base_url if confluence_config else "",
        "CONFLUENCE_USER_EMAIL": confluence_config.email if confluence_config else "",
        "CONFLUENCE_SPACE_KEYS": ",".join(confluence_config.normalized_space_keys) if confluence_config else "",
        "CONFLUENCE_API_TOKEN_SCOPE": confluence_config.credential_scope if confluence_config else "",
        "CONFLUENCE_TICKET_SEARCH_ALLOWED": str(confluence_consent).lower(),
        "CONFLUENCE_SEARCH_CONSENT_SCOPE": confluence_config.consent_scope if confluence_config and confluence_consent else "",
        "JSM_AUTO_TRIAGE_NEW_TICKETS": str(settings.auto_triage_new_tickets).lower(),
        "JSM_POLL_INTERVAL_SECONDS": str(settings.poll_interval_seconds),
        "JSM_AUTO_TRIAGE_MAX_PER_POLL": str(settings.max_auto_triage_per_poll),
        "SUPPORT_N1_KEYWORDS": settings.n1_keywords.strip(),
        "SUPPORT_N3_PLUS_KEYWORDS": settings.n3_plus_keywords.strip(),
        "SUPPORT_N3_KEYWORDS": settings.n3_keywords.strip(),
    }
    env_values.update(model_env_values)
    try:
        async with SETUP_WRITE_LOCK:
            _persist_local_setup(
                env_values,
                settings.jira_api_token.strip(),
                remove_api_token=not any(jira_fields),
                model_api_key=model_api_key,
                remove_model_api_key=remove_model_api_key,
                confluence_api_token=confluence_token,
                remove_confluence_api_token=knowledge_provider != "confluence",
            )
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Local setup could not be saved. Check that the project folder is writable.") from exc

    return {
        "saved": True,
        "jira_connected": bool(any(jira_fields) and token),
        "jira_queue_sample_count": queue_sample_count,
        "knowledge": knowledge_status(),
        "confluence_connection": "configured_not_checked" if knowledge_provider == "confluence" else "not_selected",
        "model_provider": model_env_values.get("MODEL_PROVIDER", ProviderConfig.from_env().provider),
        "model_name": model_env_values.get("MODEL_NAME", ProviderConfig.from_env().model),
        "external_model_disclosure_accepted": model_env_values.get(
            "ALLOW_EXTERNAL_TICKET_INFERENCE",
            str(ProviderConfig.from_env().external_ticket_inference_allowed).lower(),
        ) == "true",
        "message": "Settings saved on this machine. Setup does not send a model request, and saved API keys are never returned to the browser.",
    }


@app.get("/api/integrations/status")
async def integrations_status() -> dict[str, object]:
    jira = JiraConfig.from_env()
    private_mode = _private_local_mode_enabled()
    adapter = (
        create_itsm_adapter("jira-service-management", jira)
        if jira.configured and not private_mode
        else None
    )
    capabilities = sorted(adapter.capabilities) if adapter else []
    return {
        "private_local_mode": private_mode,
        "itsm_provider": "demo" if private_mode else "jira-service-management" if jira.configured else "demo",
        "jira_configured": jira.configured and not private_mode,
        "itsm_capabilities": capabilities,
        "jira_write_action": (
            "public-comment-after-two-human-confirmations"
            if "public_reply" in capabilities
            else "not_configured"
        ),
        "github_configured": False,
        "github_write_action": "not_implemented",
        "external_ticket_inference_allowed": (
            False
            if private_mode
            else ProviderConfig.from_env().external_ticket_inference_allowed
        ),
        "knowledge": knowledge_status(),
        "jira_monitor": {
            **JIRA_MONITOR_STATE,
            "interval_seconds": _monitor_interval_seconds(),
        },
    }


@app.post("/api/itsm/sync", response_model=list[CaseRecord])
async def sync_itsm_queue() -> list[CaseRecord]:
    if _private_local_mode_enabled():
        raise HTTPException(
            status_code=403,
            detail="Jira sync is disabled while PRIVATE_LOCAL_MODE is active.",
        )
    config = JiraConfig.from_env()
    try:
        async with JIRA_MONITOR_LOCK:
            remote_cases = await create_itsm_adapter("jira-service-management", config).list_queue_cases()
    except ITSMConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ITSMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    for case in remote_cases:
        CASES[case.id] = case.model_copy(
            update={
                "tags": sorted(set(case.tags) | JIRA_LOCAL_TAGS.get(case.id, set()))
            }
        )
        _save_case(CASES[case.id])
    return remote_cases


@app.get("/api/cases", response_model=list[CaseRecord])
async def list_cases() -> list[CaseRecord]:
    return list(CASES.values())


@app.get("/api/metrics", response_model=WorkspaceMetrics)
async def workspace_metrics() -> WorkspaceMetrics:
    runs = list(RUNS.values())
    elapsed_seconds = [run.elapsed_ms / 1000 for run in runs]
    return WorkspaceMetrics(
        open_cases=sum(
            case.status.strip().lower() not in {"closed", "resolved", "done"}
            for case in CASES.values()
        ),
        total_cases=len(CASES),
        workflow_runs=len(runs),
        live_model_runs=sum(run.provider_mode == "live" for run in runs),
        drafts_passing_gate=sum(run.score >= 9 for run in runs),
        reviewer_approved_runs=sum(run.status == "approved" for run in runs),
        reviewer_edited_runs=sum(run.human_edited for run in runs),
        reviewer_rejected_runs=sum(run.status == "rejected" for run in runs),
        public_replies_published=sum(run.ticket_comment_published for run in runs),
        median_triage_seconds=(
            round(float(median(elapsed_seconds)), 1) if elapsed_seconds else None
        ),
    )


@app.get("/api/product-signals")
async def product_signals() -> list[dict[str, object]]:
    """Group repeated model-inferred triage categories for human product review."""
    latest_runs: dict[str, WorkflowResult] = {}
    for run in RUNS.values():
        if run.status != "failed":
            latest_runs[run.case_id] = run

    grouped: dict[str, list[dict[str, object]]] = {}
    for case_id, run in latest_runs.items():
        category = run.category.strip()
        if category.lower() in {
            "",
            "needs triage",
            "unknown",
        }:
            continue
        case = CASES.get(case_id)
        if case is None:
            continue
        grouped.setdefault(category, []).append(
            {
                "id": case.id,
                "title": case.title,
                "source": case.source,
                "confidence": run.confidence,
            }
        )

    return [
        {
            "category": category,
            "cases": sorted(items, key=lambda item: str(item["id"])),
        }
        for category, items in sorted(
            grouped.items(), key=lambda entry: (-len(entry[1]), entry[0].lower())
        )
        if len(items) >= 2
    ]


@app.post("/api/cases", response_model=CaseRecord, status_code=201)
async def create_case(request: IntakeRequest) -> CaseRecord:
    next_number = max(
        (int(key[4:]) for key in CASES if key.startswith("LAB-") and key[4:].isdigit()),
        default=0,
    ) + 1
    case_id = f"LAB-{next_number:04d}"
    case = CaseRecord(
        id=case_id,
        title=request.title.strip(),
        requester=request.requester.strip(),
        channel="Local lab",
        created_at=datetime.now(UTC).isoformat(timespec="minutes"),
        priority="Untriaged",
        category="Needs triage",
        status="New",
        body=request.body.strip(),
        impact=request.impact.strip(),
        tags=["synthetic-lab"],
    )
    CASES[case_id] = case
    _save_case(case)
    return case


@app.post("/api/workflows", response_model=WorkflowResult, status_code=201)
async def start_workflow(request: RunRequest) -> WorkflowResult:
    async with _workflow_lock(request.case_id):
        return await _start_workflow_locked(request)


@app.post(
    "/api/v1/workflows/{workflow_id}/runs",
    response_model=WorkflowResult,
    status_code=201,
)
async def start_published_workflow(
    workflow_id: str, request: PublishedWorkflowRunRequest
) -> WorkflowResult:
    if request.include_model_council and _private_local_mode_enabled():
        raise HTTPException(
            status_code=403,
            detail="Model council is disabled in PRIVATE_LOCAL_MODE.",
        )
    registry = configured_workflow_registry()
    try:
        published = registry.get_published(workflow_id, request.workflow_version)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if published is None:
        raise HTTPException(status_code=404, detail="Published workflow version not found.")
    if published.content_hash != request.workflow_content_hash:
        raise HTTPException(status_code=409, detail="Published workflow hash changed; reload before running.")
    try:
        plan = compile_published_workflow(
            published,
            expected_version=request.workflow_version,
            expected_content_hash=request.workflow_content_hash,
            provider=ProviderConfig.from_env(),
        )
    except ExecutionPlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    run_request = RunRequest(
        case_id=request.case_id,
        candidate_count=request.candidate_count,
        include_model_council=request.include_model_council,
    )
    async with _workflow_lock(request.case_id):
        # Setup could have changed while waiting for another run on this case.
        try:
            current = compile_published_workflow(
                published,
                expected_version=request.workflow_version,
                expected_content_hash=request.workflow_content_hash,
                provider=ProviderConfig.from_env(),
            )
        except ExecutionPlanError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if current != plan:
            raise HTTPException(status_code=409, detail="Workflow execution bindings changed; reload and validate again.")
        store = STATE_STORE or configured_store()
        lease_owner = uuid4().hex
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "candidate_count": request.candidate_count,
                    "include_model_council": request.include_model_council,
                    "bindings_fingerprint": plan.bindings_fingerprint,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            claim = store.claim_published_run(
                idempotency_key=request.idempotency_key,
                proposed_run_id=f"RUN-{uuid4().hex[:8].upper()}",
                case_id=request.case_id,
                workflow_id=workflow_id,
                workflow_version=request.workflow_version,
                workflow_content_hash=request.workflow_content_hash,
                request_fingerprint=request_fingerprint,
                lease_owner=lease_owner,
                now=datetime.now(UTC),
                lease_seconds=3600,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if claim["state"] == "completed":
            cached = RUNS.get(str(claim["run_id"])) or store.load_runs().get(str(claim["run_id"]))
            if cached is None:
                raise HTTPException(status_code=409, detail="The completed idempotent run is missing from local storage.")
            RUNS[cached.id] = cached
            return cached
        if claim["state"] == "in_progress":
            raise HTTPException(status_code=409, detail="This idempotency key already has a run in progress.")
        try:
            return await _start_workflow_locked(
                run_request,
                execution_plan=plan,
                published_snapshot=published,
                execution_claim=(request.idempotency_key, lease_owner),
                execution_run_id=str(claim["run_id"]),
                execution_attempt=int(claim["attempt"]),
                stage_callback=lambda stage, ordinal: _record_published_execution_stage(
                    store, request.idempotency_key, lease_owner, str(claim["run_id"]),
                    int(claim["attempt"]), stage, ordinal,
                ),
            )
        except Exception:
            store.fail_published_run_claim(request.idempotency_key, lease_owner, datetime.now(UTC))
            raise


async def _start_workflow_locked(
    request: RunRequest,
    *,
    execution_plan: ExecutionPlan | None = None,
    published_snapshot: PublishedWorkflow | None = None,
    execution_claim: tuple[str, str] | None = None,
    execution_run_id: str | None = None,
    execution_attempt: int = 1,
    stage_callback: Callable[[StageRecord, int], None] | None = None,
) -> WorkflowResult:
    case = CASES.get(request.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if execution_plan is not None and (
        case.source != "jira-service-management" or not case.external_key
    ):
        raise HTTPException(
            status_code=409,
            detail="A published Jira workflow can run only on a case linked to a Jira Service Management request.",
        )
    if _private_local_mode_enabled() and request.include_model_council:
        raise HTTPException(
            status_code=403,
            detail="Model council and judge profiles are disabled in PRIVATE_LOCAL_MODE.",
        )
    if case.source == "jira-service-management":
        knowledge = knowledge_status()
        if (
            knowledge["mode"] not in {"local", "confluence"}
            or not knowledge["configured"]
            or not knowledge["available"]
        ):
            raise HTTPException(
                status_code=409,
                detail="Jira triage requires an available local or approved Confluence knowledge source. Configure and authorize a knowledge source before processing tickets.",
            )
    config = ProviderConfig.from_env()
    if execution_plan is not None and f"{config.provider}:{config.model}" != execution_plan.model_ref:
        raise HTTPException(status_code=409, detail="Active model changed before execution could start.")
    if (
        config.requires_external_ticket_consent
        and not config.external_ticket_inference_allowed
    ):
        raise HTTPException(
            status_code=403,
            detail="External ticket inference is off. Review provider terms and confirm the selected provider and endpoint in Setup before sending ticket text to a model.",
        )
    if (
        config.requires_external_ticket_consent
        and not config.live
    ):
        raise HTTPException(
            status_code=409,
            detail="The selected external model credentials are not configured for this provider destination. Re-enter the API key in Setup before processing Jira tickets.",
        )
    if execution_plan is not None:
        if published_snapshot is None:
            raise HTTPException(status_code=409, detail="The published workflow snapshot is unavailable.")
        try:
            current_plan = compile_published_workflow(
                published_snapshot,
                expected_version=execution_plan.version,
                expected_content_hash=execution_plan.content_hash,
                provider=config,
            )
        except ExecutionPlanError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if current_plan != execution_plan:
            raise HTTPException(
                status_code=409,
                detail="Connector, knowledge, or model bindings changed before execution. Reload and validate the workflow again.",
            )
    source_snapshot_hash: str | None = None
    if case.source == "jira-service-management":
        try:
            details = await create_itsm_adapter("jira-service-management").get_request_details(case.external_key or case.id)
            case = case.model_copy(
                update={
                    "title": details["summary"],
                    "body": details["description"],
                    "status": str(details.get("status") or case.status),
                }
            )
            source_snapshot_hash = _source_snapshot_hash(details, case)
            CASES[case.id] = case
            _save_case(case)
        except ITSMConfigurationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ITSMRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    try:
        result = await run_workflow(
            case,
            config,
            request.candidate_count,
            request.include_model_council,
            source_snapshot_hash=source_snapshot_hash,
            agent_role=execution_plan.agent_role if execution_plan else None,
            agent_instructions=execution_plan.agent_instructions if execution_plan else None,
            run_id=execution_run_id,
            stage_callback=stage_callback,
        )
    except KnowledgeConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KnowledgeRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    RUNS[result.id] = result
    if execution_plan is not None:
        result.workflow_id = execution_plan.workflow_id
        result.workflow_version = execution_plan.version
        result.workflow_content_hash = execution_plan.content_hash
        result.workflow_node_ids = list(execution_plan.node_ids)
    _save_run(
        result,
        audit_event=(
            result.id,
            result.case_id,
            "workflow.started",
            {
                "to_status": result.status,
                "score": result.score,
                **(
                    {
                        "workflow_id": execution_plan.workflow_id,
                        "workflow_version": execution_plan.version,
                        "workflow_content_hash": execution_plan.content_hash,
                        "workflow_node_ids": list(execution_plan.node_ids),
                    }
                    if execution_plan
                    else {}
                ),
            },
        ),
        execution_claim=execution_claim,
    )
    if case.source == "jira-service-management":
        for tag in (
            "studio-triage-paused-knowledge",
            "studio-triage-paused-consent",
            "studio-triage-failed",
            "studio-triage-retry-pending",
        ):
            _tag_local_case(case.id, tag, False)
    return result


@app.post("/api/workflows/{run_id}/publish-ticket-reply")
async def publish_ticket_reply(run_id: str) -> dict[str, object]:
    if _private_local_mode_enabled():
        raise HTTPException(
            status_code=403,
            detail="External ticket writes are disabled while PRIVATE_LOCAL_MODE is active.",
        )
    lock = PUBLISH_LOCKS.setdefault(run_id, asyncio.Lock())
    async with lock:
        return await _publish_ticket_reply_locked(run_id)


async def _publish_ticket_reply_locked(run_id: str) -> dict[str, object]:
    result = RUNS.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if (
        result.status not in {"approved", "edited"}
        or result.score < 9
        or not result.draft.strip()
    ):
        raise HTTPException(
            status_code=409,
            detail="A human must approve the 9/10+ draft before publishing",
        )
    if result.ticket_comment_published:
        raise HTTPException(status_code=409, detail="This reply was already published")
    if result.ticket_comment_outcome == "unknown":
        raise HTTPException(
            status_code=409, detail="Check the ticket manually before any retry"
        )
    case = CASES.get(result.case_id)
    if (
        case is None
        or case.source != "jira-service-management"
        or not case.external_key
    ):
        raise HTTPException(
            status_code=409,
            detail="This case is not linked to a Jira Service Management request",
        )
    try:
        latest_details = await create_itsm_adapter("jira-service-management").get_request_details(case.external_key)
    except ITSMConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ITSMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not _matches_reviewed_ticket(result, case, latest_details):
        raise HTTPException(
            status_code=409,
            detail="The Jira request changed after this workflow reviewed it. Sync and rerun triage, then approve the new draft before publishing.",
        )
    # An interrupted request is ambiguous. Keep this run closed to retries.
    result.ticket_comment_outcome = "unknown"
    _save_run(
        result,
        audit_event=(
            result.id,
            result.case_id,
            "publish.attempted",
            {
                "from_status": result.status,
                "to_status": result.status,
                "decision_version": result.approval_version,
                "outcome": "unknown",
            },
        ),
    )
    try:
        posted = await create_itsm_adapter("jira-service-management").publish_public_reply(case.external_key, result.draft)
    except ITSMConfigurationError as exc:
        result.ticket_comment_outcome = "failed"
        _save_run(
            result,
            audit_event=(result.id, result.case_id, "publish.failed", {"outcome": "failed"}),
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ITSMOutcomeUnknown as exc:
        result.ticket_comment_outcome = "unknown"
        _save_run(
            result,
            audit_event=(result.id, result.case_id, "publish.unknown", {"outcome": "unknown"}),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ITSMRequestError as exc:
        result.ticket_comment_outcome = "failed"
        _save_run(
            result,
            audit_event=(result.id, result.case_id, "publish.failed", {"outcome": "failed"}),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result.ticket_comment_published = True
    result.ticket_comment_id = posted.get("id")
    result.ticket_comment_outcome = "published"
    _save_run(
        result,
        audit_event=(result.id, result.case_id, "publish.succeeded", {"outcome": "published"}),
    )
    return {"published": True, "comment_id": result.ticket_comment_id, "public": True}


def _matches_reviewed_ticket(
    result: WorkflowResult,
    case: CaseRecord,
    latest_details: dict[str, object],
) -> bool:
    summary = latest_details.get("summary")
    description = latest_details.get("description")
    status = latest_details.get("status")
    refreshed_hash = latest_details.get("source_snapshot_hash")
    if (
        not isinstance(refreshed_hash, str)
        or len(refreshed_hash) != 64
        or any(character not in "0123456789abcdef" for character in refreshed_hash.lower())
    ):
        refreshed_hash = (
            content_fingerprint(summary, description, status)
            if isinstance(summary, str)
            and isinstance(description, str)
            and isinstance(status, str)
            else None
        )
    return (
        isinstance(summary, str)
        and isinstance(description, str)
        and isinstance(status, str)
        and summary == case.title
        and description == case.body
        and status == case.status
        and bool(result.source_snapshot_hash)
        and refreshed_hash == result.source_snapshot_hash
    )


def _source_snapshot_hash(
    details: dict[str, object], case: CaseRecord
) -> str:
    candidate = details.get("source_snapshot_hash")
    if (
        isinstance(candidate, str)
        and len(candidate) == 64
        and all(character in "0123456789abcdef" for character in candidate.lower())
    ):
        return candidate.lower()
    return content_fingerprint(
        str(details.get("summary", case.title)),
        str(details.get("description", case.body)),
        str(details.get("status", case.status)),
    )


async def _require_current_ticket_snapshot(
    result: WorkflowResult,
    case: CaseRecord,
) -> None:
    """Fail closed when Jira content is no longer the content reviewed by this run."""
    if case.source != "jira-service-management":
        return
    if not case.external_key or not result.source_snapshot_hash:
        raise HTTPException(
            status_code=409,
            detail="This Jira run has no verified source snapshot. Sync and rerun triage before approval.",
        )
    try:
        latest_details = await create_itsm_adapter(
            "jira-service-management"
        ).get_request_details(case.external_key)
    except ITSMConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ITSMRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not _matches_reviewed_ticket(result, case, latest_details):
        raise HTTPException(
            status_code=409,
            detail="The Jira request changed after this workflow reviewed it. Sync and rerun triage before approving or re-reviewing the draft.",
        )


@app.get("/api/workflows/latest/{case_id}", response_model=WorkflowResult)
async def latest_workflow(case_id: str) -> WorkflowResult:
    result = next(
        (item for item in reversed(list(RUNS.values())) if item.case_id == case_id),
        None,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No workflow run for this case")
    return result


@app.get("/api/workflows/{run_id}/events")
async def workflow_run_events(run_id: str) -> list[dict[str, object]]:
    """Read metadata-only local audit events; no ticket or draft text is returned."""
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if STATE_STORE is None:
        return []
    return STATE_STORE.list_run_events(run_id)


@app.get("/api/workflows/{run_id}/execution-steps")
async def workflow_execution_steps(run_id: str) -> list[dict[str, object]]:
    """Return content-free per-attempt stage checkpoints for local recovery inspection."""
    if STATE_STORE is None:
        raise HTTPException(status_code=503, detail="Local run storage is not ready.")
    return STATE_STORE.list_execution_steps(run_id)


@app.post("/api/workflows/{run_id}/decision", response_model=WorkflowResult)
async def decide(run_id: str, request: DecisionRequest) -> WorkflowResult:
    lock = DECISION_LOCKS.setdefault(run_id, asyncio.Lock())
    async with lock:
        return await _decide_locked(run_id, request)


async def _decide_locked(
    run_id: str, request: DecisionRequest
) -> WorkflowResult:
    result = RUNS.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if result.status != "awaiting_human":
        raise HTTPException(
            status_code=409, detail="This run already has a human decision"
        )
    if request.expected_version != result.approval_version:
        raise HTTPException(
            status_code=409,
            detail="The draft changed after this action was prepared. Review the latest draft and submit your decision again.",
        )
    case = CASES.get(result.case_id)
    if request.decision in {"approve", "edit"}:
        if case is None:
            raise HTTPException(status_code=404, detail="Workflow case not found")
        await _require_current_ticket_snapshot(result, case)
    if request.decision == "approve" and (result.score < 9 or not result.draft.strip()):
        raise HTTPException(
            status_code=409,
            detail="The 9/10 quality gate must pass before approving a draft",
        )
    if request.decision == "edit":
        if not request.edited_draft or not request.edited_draft.strip():
            raise HTTPException(status_code=422, detail="Edited draft cannot be empty")
        if result.revision_count >= 3:
            raise HTTPException(
                status_code=409,
                detail="The bounded revision limit is reached. Start a new workflow before further edits.",
            )
        assert case is not None
        config = ProviderConfig.from_env()
        if (
            config.requires_external_ticket_consent
            and not config.external_ticket_inference_allowed
        ):
            raise HTTPException(
                status_code=403,
                detail="External ticket inference is off. Review provider terms and confirm the selected provider and endpoint in Setup before asking a model to review an edited reply.",
            )
        if (
            config.requires_external_ticket_consent
            and not config.live
        ):
            raise HTTPException(
                status_code=409,
                detail="The selected external model credentials are not configured for this provider destination. Re-enter the API key in Setup before reviewing an edited reply.",
            )
        try:
            score, verifier_findings, reviewer_findings = await review_edited_draft(
                case,
                request.edited_draft.strip(),
                result.evidence,
                config,
            )
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        result.approval_version += 1
        result.draft = request.edited_draft.strip()
        result.score = score
        result.verifier_findings = verifier_findings
        result.reviewer_findings = reviewer_findings
        result.revision_count += 1
        result.human_edited = True
        result.human_decision = "Edited by reviewer; re-reviewed, approval still required"
        prior_decision_stage = next(
            (stage for stage in reversed(result.stages) if stage.id.startswith("human")),
            None,
        )
        if prior_decision_stage is not None:
            prior_decision_stage.id = f"human-edit-submitted-{result.revision_count}"
            prior_decision_stage.label = "Human edit submitted"
            prior_decision_stage.status = "complete"
            prior_decision_stage.detail = "Reviewer edited the draft; re-review and a new approval are required."
        result.stages.extend(
            [
                StageRecord(
                    id=f"human-edit-verify-{result.revision_count}",
                    label="Independent verifier · human edit",
                    status="complete",
                    detail=(
                        "Demo mode used deterministic checks; no model call."
                        if not config.live
                        else f"Fresh-context verifier returned {len(verifier_findings)} finding(s)."
                    ),
                    actor="Verifier · read-only",
                    elapsed_ms=0,
                ),
                StageRecord(
                    id=f"human-edit-review-{result.revision_count}",
                    label="Specialist review · human edit",
                    status="complete",
                    detail="Three review lanes re-scored the edited draft.",
                    actor="Correctness · security · clarity",
                    elapsed_ms=0,
                ),
                StageRecord(
                    id=f"human-edit-gate-{result.revision_count}",
                    label="Quality gate · human edit",
                    status="review" if score >= 9 else "warning",
                    detail=(
                        f"{score:.1f}/10 after human edit · explicit approval required"
                        if score >= 9
                        else f"{score:.1f}/10 after human edit · revise again or start a new run"
                    ),
                    actor="Deterministic rubric + specialist findings",
                    elapsed_ms=0,
                ),
                StageRecord(
                    id=f"human-approval-{result.approval_version}",
                    label="Human decision · edited draft",
                    status="pending",
                    detail="Inspect the re-reviewed draft and explicitly approve, edit, or reject it.",
                    actor="Support reviewer",
                    elapsed_ms=0,
                ),
            ]
        )
    elif request.decision == "approve":
        result.status = "approved"
        result.human_decision = "Approved by reviewer; not sent"
    else:
        result.status = "rejected"
        result.human_decision = "Rejected by reviewer"
    if request.decision != "edit":
        decision_stage = next(
            (stage for stage in reversed(result.stages) if stage.id.startswith("human")),
            None,
        )
        if decision_stage is not None:
            decision_stage.status = "complete" if request.decision == "approve" else "warning"
            decision_stage.detail = f"{result.human_decision}. No external action was taken."
    event_type = {
        "approve": "decision.approved",
        "reject": "decision.rejected",
        "edit": "draft.edited",
    }[request.decision]
    _save_run(
        result,
        audit_event=(
            result.id,
            result.case_id,
            event_type,
            {
                "from_status": "awaiting_human",
                "to_status": result.status,
                "decision_version": result.approval_version,
                "score": result.score,
            },
        ),
    )
    return result


FRONTEND_BUILD_DIR = Path(__file__).resolve().parent / "static"
if FRONTEND_BUILD_DIR.is_dir() and (FRONTEND_BUILD_DIR / "index.html").is_file():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_BUILD_DIR, html=True),
        name="frontend",
    )
