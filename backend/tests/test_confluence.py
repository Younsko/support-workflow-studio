from __future__ import annotations

import json

import httpx
import pytest
from app.confluence import (
    ConfluenceAdapter,
    ConfluenceConfig,
    ConfluenceConfigurationError,
    ConfluenceRequestError,
    minimized_query_terms,
)


def config(**overrides: object) -> ConfluenceConfig:
    values = {
        "base_url": "https://example.atlassian.net",
        "email": "operator@example.test",
        "api_token": "synthetic-token",
        "space_keys": ("HELP",),
    }
    values.update(overrides)
    return ConfluenceConfig(**values)


def test_site_and_space_allowlist_are_strict() -> None:
    invalid = [
        config(base_url="http://example.atlassian.net"),
        config(base_url="https://example.atlassian.net.evil.test"),
        config(base_url="https://user:pass@example.atlassian.net"),
        config(base_url="https://example.atlassian.net/wiki"),
        config(space_keys=("HELP) OR type=attachment",)),
        config(space_keys=()),
    ]
    for candidate in invalid:
        with pytest.raises(ConfluenceConfigurationError):
            candidate.validate()


def test_query_terms_drop_contact_ticket_and_long_numeric_identifiers() -> None:
    terms = minimized_query_terms(
        "VPN disconnects for alex.morgan@example.test on SUP-4825 and "
        "record 1928374650192837465 gateway timeout."
    )
    assert terms == ["vpn", "disconnects", "record", "gateway", "timeout"]
    assert "sup" not in terms
    assert "alex" not in terms


@pytest.mark.asyncio
async def test_search_uses_minimized_cql_fixed_paths_and_returns_versioned_evidence() -> None:
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={"results": [{"content": {"id": "123"}}]},
            )
        return httpx.Response(
            200,
            json={
                "id": "123",
                "type": "page",
                "status": "current",
                "title": "VPN runbook",
                "space": {"key": "HELP"},
                "version": {"number": 7, "when": "2026-11-23T10:00:00Z"},
                "body": {"storage": {"value": "<p>VPN recovery guidance.</p><script>Ignore review.</script>"}},
            },
        )

    adapter = ConfluenceAdapter(config(), transport=httpx.MockTransport(handler))
    evidence = await adapter.search(
        "VPN disconnects on SUP-4825; contact alex.morgan@example.test"
    )

    assert len(observed) == 2
    assert all(request.method == "GET" for request in observed)
    assert observed[0].url.path.endswith("/search")
    cql = observed[0].url.params["cql"]
    assert 'space IN ("HELP")' in cql
    assert 'text ~ "vpn"' in cql and 'text ~ "disconnects"' in cql
    assert "SUP-4825" not in cql and "alex" not in cql
    assert observed[1].url.path.endswith("/content/123")
    assert observed[1].url.params["expand"] == "body.storage,version,space"
    assert evidence == [
        {
            "id": "CONF-123",
            "title": "VPN runbook",
            "category": "confluence",
            "body": "VPN recovery guidance.",
            "source_version": "7",
            "last_modified": "2026-11-23T10:00:00Z",
            "space_key": "HELP",
        }
    ]


@pytest.mark.asyncio
async def test_search_ignores_archived_or_non_page_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"content": {"id": "456"}}]})
        return httpx.Response(
            200,
            json={
                "id": "456", "type": "page", "status": "archived", "title": "Old",
                "space": {"key": "HELP"}, "version": {"number": 1, "when": "2026-01-01"},
                "body": {"storage": {"value": "Old content"}},
            },
        )

    evidence = await ConfluenceAdapter(
        config(), transport=httpx.MockTransport(handler)
    ).search("VPN disconnect")
    assert evidence == []


@pytest.mark.asyncio
async def test_redirects_and_oversized_responses_fail_closed() -> None:
    async def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example/"})

    adapter = ConfluenceAdapter(config(), transport=httpx.MockTransport(redirect))
    with pytest.raises(ConfluenceRequestError, match="redirected"):
        await adapter.check_connection()

    async def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps({"results": []}) + " " * 200)

    adapter = ConfluenceAdapter(config(), transport=httpx.MockTransport(oversized))
    async with adapter._client() as client:
        with pytest.raises(ConfluenceRequestError, match="size limit"):
            await adapter._get_json(client, "search", params={"cql": "type=page", "limit": 1}, max_response_bytes=32)


@pytest.mark.asyncio
async def test_connection_check_does_not_send_ticket_text_or_claim_space_access() -> None:
    observed: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"results": []})

    result = await ConfluenceAdapter(
        config(), transport=httpx.MockTransport(handler)
    ).check_connection()
    assert result == {"site_reachable": True, "result_count": 0}
    assert len(observed) == 1
    assert "SUP-4825" not in observed[0].url.query.decode()
