from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key: str
    base_url: str
    timeout: float
    max_calls: int

    @classmethod
    def from_env(cls) -> ProviderConfig:
        explicit_provider = os.getenv("MODEL_PROVIDER", "").strip().lower()
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        provider = explicit_provider or "demo"
        base_url = os.getenv("MODEL_BASE_URL", "").strip().rstrip("/")
        if provider == "openai-compatible" and not base_url and groq_key:
            base_url = "https://api.groq.com/openai/v1"
        groq_endpoint = (urlparse(base_url).hostname or "").casefold() == "api.groq.com"
        api_key = os.getenv("MODEL_API_KEY", "").strip()
        if not api_key and provider == "openai-compatible" and groq_endpoint:
            api_key = groq_key
        model = os.getenv("MODEL_NAME", "").strip()
        if not model:
            model = (
                "openai/gpt-oss-20b"
                if provider == "openai-compatible" and groq_endpoint
                else "Synthetic workflow model"
            )
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            max_calls=max(1, min(40, int(os.getenv("MAX_MODEL_CALLS_PER_RUN", "24")))),
        )

    @property
    def live(self) -> bool:
        if self.provider == "ollama-local":
            return bool(self.model and _is_ollama_local_endpoint(self.base_url))
        return self.provider in {"openai-compatible", "anthropic"} and bool(
            self.api_key
        )

    @property
    def requires_external_ticket_consent(self) -> bool:
        return self.live and not (
            self.provider == "ollama-local"
            or (
                self.provider == "openai-compatible"
                and _is_loopback_endpoint(self.base_url)
            )
        )

    @property
    def external_ticket_inference_allowed(self) -> bool:
        return os.getenv(
            "ALLOW_EXTERNAL_TICKET_INFERENCE", "false"
        ).strip().lower() in {
            "1",
            "true",
            "yes",
        }

    @classmethod
    def council_profile(cls) -> ProviderConfig | None:
        provider = os.getenv("COUNCIL_PROVIDER", "").strip().lower()
        model = os.getenv("COUNCIL_MODEL", "").strip()
        api_key = os.getenv("COUNCIL_API_KEY", "").strip()
        if not provider or not model or not api_key:
            return None
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=os.getenv("COUNCIL_BASE_URL", "").strip().rstrip("/"),
            timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            max_calls=2,
        )

    @classmethod
    def judge_profile(cls) -> ProviderConfig | None:
        provider = os.getenv("JUDGE_PROVIDER", "").strip().lower()
        model = os.getenv("JUDGE_MODEL", "").strip()
        api_key = os.getenv("JUDGE_API_KEY", "").strip()
        if not provider or not model or not api_key:
            return None
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=os.getenv("JUDGE_BASE_URL", "").strip().rstrip("/"),
            timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "30")),
            max_calls=1,
        )


class ProviderError(RuntimeError):
    pass


def _is_loopback_endpoint(base_url: str) -> bool:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    try:
        address = ip_address(parsed.hostname)
    except ValueError:
        return parsed.hostname.lower() == "localhost"
    return address.is_loopback


def _is_ollama_local_endpoint(base_url: str) -> bool:
    if not _is_loopback_endpoint(base_url):
        return False
    parsed = urlparse(base_url)
    try:
        return (
            not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and parsed.port in (None, 80, 11434)
        )
    except ValueError:
        return False


def _ollama_text(response_json: object) -> str:
    if not isinstance(response_json, dict):
        raise ProviderError("Model returned an invalid response shape.")
    message = response_json.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ProviderError("Model returned an invalid response shape.")
    return message["content"]


def _validate_local_ollama_model(response_json: object, requested_model: str) -> None:
    if not isinstance(response_json, dict):
        raise ProviderError("Ollama local model list has an invalid response shape.")
    models = response_json.get("models")
    if not isinstance(models, list):
        raise ProviderError("Ollama local model list has an invalid response shape.")
    for model in models:
        if not isinstance(model, dict) or model.get("name") != requested_model:
            continue
        # Ollama Cloud model tags are represented by a tiny metadata stub in
        # /api/tags; rejecting small entries prevents a local daemon from
        # silently routing ticket prompts to its cloud service.
        size = model.get("size")
        if isinstance(size, int) and size >= 16 * 1024 * 1024:
            return
        break
    raise ProviderError(
        "The selected Ollama model is not present as a local model; remote/cloud model tags are refused."
    )


def _anthropic_text(response_json: object) -> str:
    if not isinstance(response_json, dict):
        raise ProviderError("Model returned an invalid response shape.")
    parts = response_json.get("content")
    if not isinstance(parts, list):
        raise ProviderError("Model returned an invalid response shape.")
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


def _model_output_limit() -> int:
    try:
        value = int(os.getenv("MODEL_MAX_OUTPUT_TOKENS", "2048"))
    except ValueError:
        value = 2048
    return max(128, min(2048, value))


def _rate_limit_delay(response: object, attempt: int) -> float:
    headers = getattr(response, "headers", {})
    reset = str(headers.get("x-ratelimit-reset-tokens", "")).strip()
    retry_after = str(headers.get("retry-after", "")).strip()
    candidate = reset or retry_after
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s|m)?", candidate, re.I)
    if match:
        amount = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        delay = amount / 1000 if unit == "ms" else amount * 60 if unit == "m" else amount
    else:
        delay = float(2**attempt)
    return max(0.5, min(60.0, delay + attempt * 0.25))


async def _post_with_rate_limit_retry(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str], json: dict[str, object]
) -> httpx.Response:
    request_body = json
    rate_limit_retries = 0
    schema_retries = 0
    for _ in range(5):
        response = await client.post(url, headers=headers, json=request_body)
        status = getattr(response, "status_code", 200)
        if status == 429 and rate_limit_retries < 2:
            await asyncio.sleep(_rate_limit_delay(response, rate_limit_retries))
            rate_limit_retries += 1
            continue
        if status == 400 and schema_retries < 2:
            try:
                body = response.json()
                error = body.get("error", {}) if isinstance(body, dict) else {}
                invalid_schema_output = (
                    isinstance(error, dict)
                    and error.get("code") == "json_validate_failed"
                    and isinstance(request_body.get("response_format"), dict)
                    and request_body["response_format"].get("type") == "json_schema"
                )
            except (AttributeError, TypeError, ValueError):
                invalid_schema_output = False
            if invalid_schema_output:
                retry_body = dict(request_body)
                messages = request_body.get("messages")
                if isinstance(messages, list):
                    retry_messages = [dict(item) for item in messages if isinstance(item, dict)]
                    if retry_messages and isinstance(retry_messages[0].get("content"), str):
                        retry_messages[0]["content"] += (
                            " Return every required field in the response schema; "
                            "include empty arrays instead of omitting fields."
                        )
                    retry_body["messages"] = retry_messages
                request_body = retry_body
                schema_retries += 1
                continue
        response.raise_for_status()
        return response
    response.raise_for_status()
    raise ProviderError("Model retry budget was exhausted.")


def _openai_compatible_text(response_json: object) -> str:
    if not isinstance(response_json, dict):
        raise ProviderError("Model returned an invalid response shape.")
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("Model returned an invalid response shape.")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ProviderError("Model returned an invalid response shape.")
    return message["content"]


async def generate(
    config: ProviderConfig,
    system: str,
    prompt: str,
    response_format: dict[str, object] | None = None,
) -> str:
    if not config.live:
        return ""
    if config.provider == "ollama-local" and not _is_ollama_local_endpoint(
        config.base_url
    ):
        raise ProviderError(
            "The ollama-local provider only allows an HTTP loopback endpoint."
        )
    if config.provider == "openai-compatible":
        parsed = urlparse(config.base_url)
        try:
            host = parsed.hostname or ""
            try:
                address = ip_address(host) if host else None
            except ValueError:
                # DNS names are valid remote providers; only literal addresses
                # need IP classification for the loopback HTTP exception.
                address = None
            loopback = bool(address and address.is_loopback) or host == "localhost"
            port = parsed.port
        except ValueError as exc:
            raise ProviderError("Model endpoint URL is invalid.") from exc
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.netloc
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
            or (parsed.scheme == "https" and port not in (None, 443))
        ):
            raise ProviderError(
                "Model endpoint must use HTTPS, or HTTP on a local loopback host, without credentials, query, or fragment."
            )
    try:
        async with httpx.AsyncClient(
            timeout=config.timeout,
            trust_env=config.provider != "ollama-local",
        ) as client:
            if config.provider == "ollama-local":
                model_list = await client.get(f"{config.base_url.rstrip('/')}/api/tags")
                model_list.raise_for_status()
                _validate_local_ollama_model(model_list.json(), config.model)
                response = await client.post(
                    f"{config.base_url.rstrip('/')}/api/chat",
                    headers={"content-type": "application/json"},
                    json={
                        "model": config.model,
                        "stream": False,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "options": {"temperature": 0.2},
                    },
                )
                response.raise_for_status()
                return _ollama_text(response.json())
            if config.provider == "anthropic":
                response = await _post_with_rate_limit_retry(
                    client,
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": config.api_key,
                        "anthropic-version": os.getenv(
                            "ANTHROPIC_API_VERSION", "2023-06-01"
                        ),
                        "content-type": "application/json",
                    },
                    json={
                        "model": config.model,
                        "max_tokens": _model_output_limit(),
                        "system": system,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                response.raise_for_status()
                return _anthropic_text(response.json())

            if config.provider == "openai-compatible" and config.base_url:
                request_body: dict[str, object] = {
                    "model": config.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                }
                host = (urlparse(config.base_url).hostname or "").casefold()
                if host == "api.groq.com":
                    request_body["max_completion_tokens"] = _model_output_limit()
                    if config.model.startswith("openai/gpt-oss-"):
                        request_body["reasoning_effort"] = os.getenv(
                            "GROQ_REASONING_EFFORT", "low"
                        )
                        request_body["reasoning_format"] = "hidden"
                else:
                    request_body["max_tokens"] = _model_output_limit()
                if response_format:
                    if (
                        response_format.get("type") == "json_schema"
                        and host != "api.groq.com"
                    ):
                        request_body["response_format"] = {"type": "json_object"}
                    else:
                        request_body["response_format"] = response_format
                response = await _post_with_rate_limit_retry(
                    client,
                    f"{config.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {config.api_key}"},
                    json=request_body,
                )
                response.raise_for_status()
                return _openai_compatible_text(response.json())
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise ProviderError(f"Model request failed: {type(exc).__name__}") from exc
    raise ProviderError(
        "Provider is not configured. Use demo mode or set provider, model, endpoint, and key."
    )
