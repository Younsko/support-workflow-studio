from __future__ import annotations

import pytest
from app.connector_sdk import (
    CONNECTOR_SDK_VERSION,
    ConnectorRegistrationError,
    ITSMConnectorRegistry,
    KnowledgeSourceAdapter,
)
from app.itsm import JiraConfig, JiraServiceManagementAdapter


def jira_config() -> JiraConfig:
    return JiraConfig(
        base_url="https://example.atlassian.net",
        email="agent@example.test",
        api_token="synthetic-token",
        service_desk_id="7",
        queue_id="12",
    )


def test_registry_constructs_an_explicitly_registered_adapter() -> None:
    registry = ITSMConnectorRegistry()
    registry.register(
        JiraServiceManagementAdapter.provider_id,
        lambda configuration: JiraServiceManagementAdapter(configuration),
    )

    adapter = registry.create(JiraServiceManagementAdapter.provider_id, jira_config())

    assert CONNECTOR_SDK_VERSION == "1.0"
    assert adapter.provider_id == "jira-service-management"
    assert adapter.capabilities == frozenset(
        {
            "workspace_discovery",
            "queue_discovery",
            "queue_read",
            "request_refresh",
            "public_reply",
        }
    )


def test_registry_rejects_invalid_duplicate_and_unknown_provider_ids() -> None:
    registry = ITSMConnectorRegistry()
    factory = lambda configuration: JiraServiceManagementAdapter(configuration)
    registry.register("jira-service-management", factory)

    with pytest.raises(ConnectorRegistrationError, match="already registered"):
        registry.register("jira-service-management", factory)
    with pytest.raises(ConnectorRegistrationError, match="ID is invalid"):
        registry.register("Jira Cloud", factory)
    with pytest.raises(ConnectorRegistrationError, match="not implemented"):
        registry.create("unknown-provider")


def test_registry_rejects_a_factory_that_returns_another_provider() -> None:
    registry = ITSMConnectorRegistry()
    registry.register(
        "custom-provider",
        lambda configuration: JiraServiceManagementAdapter(configuration),
    )

    with pytest.raises(ConnectorRegistrationError, match="incompatible adapter"):
        registry.create("custom-provider", jira_config())


def test_registry_rejects_unrecognized_capabilities() -> None:
    class UnsupportedCapabilityAdapter(JiraServiceManagementAdapter):
        capabilities = frozenset({"execute_arbitrary_code"})

    registry = ITSMConnectorRegistry()
    registry.register(
        "jira-service-management",
        lambda configuration: UnsupportedCapabilityAdapter(configuration),
    )

    with pytest.raises(ConnectorRegistrationError, match="unsupported capabilities"):
        registry.create("jira-service-management", jira_config())


def test_confluence_implements_the_read_only_knowledge_contract() -> None:
    from app.confluence import ConfluenceAdapter, ConfluenceConfig

    adapter = ConfluenceAdapter(
        ConfluenceConfig(
            base_url="https://example.atlassian.net",
            email="agent@example.test",
            api_token="synthetic-token",
            space_keys=("HELP",),
        )
    )

    assert isinstance(adapter, KnowledgeSourceAdapter)
    assert adapter.provider_id == "confluence-cloud"
    assert adapter.capabilities == frozenset(
        {"connection_check", "search", "versioned_evidence"}
    )
