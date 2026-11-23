from __future__ import annotations

import asyncio
from typing import Self

import pytest
from app import providers
from app.providers import ProviderConfig, ProviderError


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class FakeAsyncClient:
    def __init__(
        self,
        *,
        response: FakeResponse,
        timeout: float,
        model_response: FakeResponse | None = None,
        **_kwargs: object,
    ) -> None:
        self.response = response
        self.model_response = model_response
        self.timeout = timeout
        self.request: dict[str, object] | None = None
        self.model_list_request: dict[str, object] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, object]
    ) -> FakeResponse:
        self.request = {"url": url, "headers": headers, "json": json}
        return self.response

    async def get(self, url: str) -> FakeResponse:
        self.model_list_request = {"url": url}
        return self.model_response or self.response




def test_groq_requires_explicit_provider(monkeypatch) -> None:
    for name in ("MODEL_PROVIDER", "MODEL_NAME", "MODEL_API_KEY", "MODEL_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")

    config = ProviderConfig.from_env()

    assert not config.live
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    config = ProviderConfig.from_env()
    assert config.provider == "openai-compatible"
    assert config.model == "openai/gpt-oss-20b"
    assert config.base_url == "https://api.groq.com/openai/v1"
    assert config.api_key == "synthetic-groq-key"
    assert config.live is True


def test_explicit_demo_mode_overrides_groq_key(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "demo")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")

    config = ProviderConfig.from_env()

    assert config.provider == "demo"
    assert config.live is False


def test_groq_key_is_not_sent_to_another_compatible_host(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MODEL_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-groq-key")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)

    config = ProviderConfig.from_env()

    assert config.base_url == "https://llm.example/v1"
    assert config.api_key == ""
    assert config.live is False


def test_saved_model_key_and_consent_are_rejected_after_endpoint_change(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MODEL_NAME", "synthetic-model")
    monkeypatch.setenv("MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-key-for-groq")
    scope = providers.model_destination_scope(
        "openai-compatible", "https://api.groq.com/openai/v1"
    )
    monkeypatch.setenv("MODEL_API_KEY_SCOPE", scope)
    monkeypatch.setenv("ALLOW_EXTERNAL_TICKET_INFERENCE", "true")
    monkeypatch.setenv("MODEL_CONSENT_SCOPE", scope)

    monkeypatch.setenv("MODEL_BASE_URL", "https://api.openai.com/v1")
    config = ProviderConfig.from_env()

    assert config.api_key == ""
    assert config.live is False
    assert config.external_ticket_inference_allowed is False

    monkeypatch.setenv("MODEL_API_KEY", "synthetic-key-for-openai")
    monkeypatch.setenv(
        "MODEL_API_KEY_SCOPE",
        providers.model_destination_scope("openai-compatible", "https://api.openai.com/v1"),
    )
    consent_only_stale = ProviderConfig.from_env()
    assert consent_only_stale.api_key == "synthetic-key-for-openai"
    assert consent_only_stale.live is True
    assert consent_only_stale.external_ticket_inference_allowed is False


def test_legacy_unscoped_model_key_and_consent_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MODEL_NAME", "synthetic-model")
    monkeypatch.setenv("MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("MODEL_API_KEY", "legacy-unscoped-synthetic-key")
    monkeypatch.delenv("MODEL_API_KEY_SCOPE", raising=False)
    monkeypatch.setenv("ALLOW_EXTERNAL_TICKET_INFERENCE", "true")
    monkeypatch.delenv("MODEL_CONSENT_SCOPE", raising=False)

    config = ProviderConfig.from_env()

    assert config.api_key == ""
    assert config.live is False
    assert config.external_ticket_inference_allowed is False


@pytest.mark.parametrize(
    ("prefix", "profile_factory"),
    [
        ("COUNCIL", ProviderConfig.council_profile),
        ("JUDGE", ProviderConfig.judge_profile),
    ],
)
def test_secondary_model_profiles_require_scoped_credentials_and_consent(
    monkeypatch, prefix, profile_factory
) -> None:
    provider = "openai-compatible"
    endpoint = "https://models.example/v1"
    scope = providers.model_destination_scope(provider, endpoint)
    monkeypatch.setenv(f"{prefix}_PROVIDER", provider)
    monkeypatch.setenv(f"{prefix}_MODEL", "synthetic-review-model")
    monkeypatch.setenv(f"{prefix}_API_KEY", "synthetic-profile-key")
    monkeypatch.setenv(f"{prefix}_BASE_URL", endpoint)

    assert profile_factory() is None

    monkeypatch.setenv(f"{prefix}_API_KEY_SCOPE", scope)
    monkeypatch.setenv(f"ALLOW_{prefix}_EXTERNAL_TICKET_INFERENCE", "true")
    monkeypatch.setenv(f"{prefix}_CONSENT_SCOPE", "stale-synthetic-scope")
    assert profile_factory() is None

    monkeypatch.setenv(f"{prefix}_CONSENT_SCOPE", scope)
    profile = profile_factory()
    assert profile is not None
    assert profile.live is True
    assert profile.external_ticket_inference_allowed is True


def test_generate_rejects_external_profile_without_destination_consent(
    monkeypatch,
) -> None:
    profile = ProviderConfig(
        provider="openai-compatible",
        model="synthetic-model",
        api_key="synthetic-key",
        base_url="https://models.example/v1",
        timeout=1,
        max_calls=1,
    )
    monkeypatch.setenv("ALLOW_EXTERNAL_TICKET_INFERENCE", "true")
    monkeypatch.setenv("MODEL_CONSENT_SCOPE", "different-synthetic-destination")

    with pytest.raises(ProviderError, match="not approved for this model destination"):
        asyncio.run(providers.generate(profile, "system", "synthetic ticket"))


def test_openai_compatible_request_and_response_shape(monkeypatch) -> None:
    response = FakeResponse({"choices": [{"message": {"content": "Draft text"}}]})
    client = FakeAsyncClient(response=response, timeout=9)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="openai-compatible",
        model="synthetic-model",
        api_key="synthetic-secret",
        base_url="https://llm.example/v1",
        timeout=9,
        max_calls=4,
        external_ticket_consent=True,
    )

    result = asyncio.run(providers.generate(config, "System", "Prompt"))

    assert result == "Draft text"
    assert client.timeout == 9
    assert client.request == {
        "url": "https://llm.example/v1/chat/completions",
        "headers": {"Authorization": "Bearer synthetic-secret"},
        "json": {
            "model": "synthetic-model",
            "messages": [
                {"role": "system", "content": "System"},
                {"role": "user", "content": "Prompt"},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
        },
    }


def test_openai_compatible_retries_rate_limit_once(monkeypatch) -> None:
    class RateResponse(FakeResponse):
        def __init__(self, payload: object, status_code: int, headers: dict[str, str]) -> None:
            super().__init__(payload)
            self.status_code = status_code
            self.headers = headers

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise AssertionError(f"unexpected HTTP status {self.status_code}")

    class RetryClient(FakeAsyncClient):
        def __init__(self) -> None:
            super().__init__(
                response=RateResponse(
                    {"choices": [{"message": {"content": "Draft text"}}]},
                    200,
                    {},
                ),
                timeout=5,
            )
            self.responses = [
                RateResponse(
                    {"error": {"type": "rate_limit_exceeded"}},
                    429,
                    {"x-ratelimit-reset-tokens": "0.5s"},
                ),
                self.response,
            ]
            self.calls = 0

        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
            self.request = {"url": url, "headers": headers, "json": json}
            response = self.responses[self.calls]
            self.calls += 1
            return response

    client = RetryClient()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(providers.asyncio, "sleep", fake_sleep)
    config = ProviderConfig(
        provider="openai-compatible",
        model="synthetic-model",
        api_key="synthetic-secret",
        base_url="https://llm.example/v1",
        timeout=5,
        max_calls=4,
        external_ticket_consent=True,
    )

    assert asyncio.run(providers.generate(config, "System", "Prompt")) == "Draft text"
    assert client.calls == 2
    assert sleeps == [0.5]


def test_anthropic_request_extracts_only_text_blocks(monkeypatch) -> None:
    response = FakeResponse(
        {
            "content": [
                {"type": "text", "text": "First"},
                {"type": "tool_use", "id": "synthetic-tool"},
                {"type": "text", "text": "Second"},
            ]
        }
    )
    client = FakeAsyncClient(response=response, timeout=11)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setenv("ANTHROPIC_API_VERSION", "2023-06-01")
    config = ProviderConfig(
        provider="anthropic",
        model="synthetic-claude",
        api_key="synthetic-secret",
        base_url="",
        timeout=11,
        max_calls=4,
        external_ticket_consent=True,
    )

    result = asyncio.run(providers.generate(config, "System", "Prompt"))

    assert result == "First\nSecond"
    assert client.request == {
        "url": "https://api.anthropic.com/v1/messages",
        "headers": {
            "x-api-key": "synthetic-secret",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        "json": {
            "model": "synthetic-claude",
            "max_tokens": 2048,
            "system": "System",
            "messages": [{"role": "user", "content": "Prompt"}],
        },
    }


def test_local_openai_compatible_endpoint_is_allowed(monkeypatch) -> None:
    response = FakeResponse({"choices": [{"message": {"content": "Local draft"}}]})
    client = FakeAsyncClient(response=response, timeout=5)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="openai-compatible",
        model="local-model",
        api_key="local-placeholder",
        base_url="http://127.0.0.1:11434/v1",
        timeout=5,
        max_calls=4,
    )

    assert asyncio.run(providers.generate(config, "System", "Prompt")) == "Local draft"
    assert client.request is not None
    assert client.request["url"] == "http://127.0.0.1:11434/v1/chat/completions"


def test_ollama_local_native_api_needs_no_key_or_external_consent(monkeypatch) -> None:
    response = FakeResponse({"message": {"role": "assistant", "content": "Local draft"}})
    model_response = FakeResponse(
        {"models": [{"name": "installed-model", "size": 1_000_000_000}]}
    )
    client = FakeAsyncClient(
        response=response, model_response=model_response, timeout=5
    )
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="ollama-local",
        model="installed-model",
        api_key="",
        base_url="http://127.0.0.1:11434",
        timeout=5,
        max_calls=4,
    )

    assert config.live is True
    assert config.requires_external_ticket_consent is False
    assert asyncio.run(providers.generate(config, "System", "Prompt")) == "Local draft"
    assert client.model_list_request == {
        "url": "http://127.0.0.1:11434/api/tags"
    }
    assert client.request == {
        "url": "http://127.0.0.1:11434/api/chat",
        "headers": {"content-type": "application/json"},
        "json": {
            "model": "installed-model",
            "stream": False,
            "messages": [
                {"role": "system", "content": "System"},
                {"role": "user", "content": "Prompt"},
            ],
            "options": {"temperature": 0.2},
        },
    }


def test_ollama_local_rejects_remote_endpoint(monkeypatch) -> None:
    response = FakeResponse({"message": {"role": "assistant", "content": "No"}})
    client = FakeAsyncClient(response=response, timeout=5)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="ollama-local",
        model="installed-model",
        api_key="",
        base_url="https://ollama.example",
        timeout=5,
        max_calls=4,
    )

    assert config.live is False
    assert asyncio.run(providers.generate(config, "System", "Prompt")) == ""
    assert client.request is None

def test_ollama_local_refuses_cloud_model_stub_before_prompt(monkeypatch) -> None:
    response = FakeResponse({"message": {"role": "assistant", "content": "No"}})
    model_response = FakeResponse({"models": [{"name": "cloud-model:cloud", "size": 384}]})
    client = FakeAsyncClient(
        response=response, model_response=model_response, timeout=5
    )
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="ollama-local",
        model="cloud-model:cloud",
        api_key="",
        base_url="http://127.0.0.1:11434",
        timeout=5,
        max_calls=4,
    )

    with pytest.raises(ProviderError, match="remote/cloud model tags are refused"):
        asyncio.run(providers.generate(config, "synthetic", "synthetic prompt"))
    assert client.model_list_request is not None
    assert client.request is None


def test_local_openai_compatible_does_not_require_external_ticket_consent() -> None:
    config = ProviderConfig(
        provider="openai-compatible",
        model="local-model",
        api_key="local-placeholder",
        base_url="http://localhost:11434/v1",
        timeout=5,
        max_calls=4,
    )

    assert config.requires_external_ticket_consent is False


def test_openai_compatible_rejects_malformed_response(monkeypatch) -> None:
    response = FakeResponse({"choices": []})
    client = FakeAsyncClient(response=response, timeout=5)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="openai-compatible",
        model="synthetic-model",
        api_key="synthetic-secret",
        base_url="https://llm.example/v1",
        timeout=5,
        max_calls=4,
        external_ticket_consent=True,
    )

    with pytest.raises(ProviderError, match="invalid response shape"):
        asyncio.run(providers.generate(config, "System", "Prompt"))


def test_openai_compatible_rejects_remote_plain_http_before_request(monkeypatch) -> None:
    response = FakeResponse({"choices": [{"message": {"content": "Must not run"}}]})
    client = FakeAsyncClient(response=response, timeout=5)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: client)
    config = ProviderConfig(
        provider="openai-compatible",
        model="synthetic-model",
        api_key="synthetic-secret",
        base_url="http://llm.example/v1",
        timeout=5,
        max_calls=4,
        external_ticket_consent=True,
    )

    with pytest.raises(ProviderError, match="must use HTTPS"):
        asyncio.run(providers.generate(config, "System", "Prompt"))
    assert client.request is None
