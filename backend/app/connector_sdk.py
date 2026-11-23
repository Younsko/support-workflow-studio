"""Small, versioned contracts for trusted in-process ITSM connector adapters.

Connector implementations are Python code trusted by the operator. This SDK
does not load plugins from workspaces or discover entry points automatically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .schemas import CaseRecord

CONNECTOR_SDK_VERSION = "1.0"
ITSM_CAPABILITIES = frozenset(
    {
        "workspace_discovery",
        "queue_discovery",
        "queue_read",
        "request_refresh",
        "public_reply",
    }
)
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")


class ConnectorRegistrationError(ValueError):
    """A connector registration or constructed adapter violates the contract."""


@runtime_checkable
class ITSMAdapter(Protocol):
    """Normalized support-ticket operations required by the workflow runtime."""

    provider_id: str
    capabilities: frozenset[str]

    async def list_workspaces(self) -> list[dict[str, str]]: ...

    async def list_queues(self, workspace_id: str) -> list[dict[str, str]]: ...

    async def validate_connection(self) -> int: ...

    async def list_queue_cases(self, limit: int = 50, start: int = 0) -> list[CaseRecord]: ...

    async def get_request_details(self, issue_key: str) -> dict[str, object]: ...

    async def publish_public_reply(self, issue_key: str, body: str) -> dict[str, str]: ...


@runtime_checkable
class KnowledgeSourceAdapter(Protocol):
    """Read-only knowledge source contract used by workflow retrieval nodes."""

    provider_id: str
    capabilities: frozenset[str]

    async def check_connection(self) -> dict[str, object]: ...

    async def search(self, ticket_text: str) -> list[dict[str, str]]: ...


ITSMAdapterFactory = Callable[[object | None], ITSMAdapter]


class ITSMConnectorRegistry:
    """Explicit registry for operator-trusted ITSM adapters.

    Each factory receives provider-specific configuration. The registry checks
    the adapter identity and capability vocabulary before returning it to the
    application. Unknown providers and duplicate registrations fail closed.
    """

    def __init__(self) -> None:
        self._factories: dict[str, ITSMAdapterFactory] = {}

    def register(self, provider_id: str, factory: ITSMAdapterFactory) -> None:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ConnectorRegistrationError("Connector provider ID is invalid.")
        if provider_id in self._factories:
            raise ConnectorRegistrationError("Connector provider ID is already registered.")
        if not callable(factory):
            raise ConnectorRegistrationError("Connector factory must be callable.")
        self._factories[provider_id] = factory

    def create(self, provider_id: str, configuration: object | None = None) -> ITSMAdapter:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ConnectorRegistrationError("Connector provider ID is invalid.")
        factory = self._factories.get(provider_id)
        if factory is None:
            raise ConnectorRegistrationError(f"ITSM provider is not implemented: {provider_id}")
        adapter = factory(configuration)
        if not isinstance(adapter, ITSMAdapter) or adapter.provider_id != provider_id:
            raise ConnectorRegistrationError("Connector factory returned an incompatible adapter.")
        capabilities = adapter.capabilities
        if (
            not isinstance(capabilities, frozenset)
            or any(not isinstance(capability, str) for capability in capabilities)
            or not capabilities <= ITSM_CAPABILITIES
        ):
            raise ConnectorRegistrationError("Connector advertises unsupported capabilities.")
        return adapter
