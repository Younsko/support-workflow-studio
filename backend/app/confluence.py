"""Read-only, space-scoped Confluence Cloud knowledge retrieval."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx


class ConfluenceConfigurationError(ValueError):
    pass


class ConfluenceRequestError(RuntimeError):
    pass


_SPACE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_CONTENT_ID = re.compile(r"^[0-9]{1,32}$")
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_UUID = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.IGNORECASE)
_MIXED_IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)[A-Za-z][A-Za-z0-9_-]{2,}\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z_-]{2,30}")
_STOPWORDS = {
    "about", "after", "also", "and", "are", "can", "could", "did", "does",
    "for", "from", "had", "has", "have", "help", "how", "into", "its",
    "just", "more", "not", "our", "please", "request", "reported", "still",
    "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "use", "using", "very", "was",
    "were", "what", "when", "where", "which", "while", "who", "will", "with",
    "would", "your", "service", "system", "issue", "device",
}
MAX_QUERY_TERMS = 8
MAX_RESULTS = 4
MAX_BODY_CHARS = 8000


@dataclass(frozen=True)
class ConfluenceConfig:
    base_url: str
    email: str
    api_token: str = field(repr=False)
    space_keys: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.email.strip() or not self.api_token.strip():
            raise ConfluenceConfigurationError("Confluence account email and API token are required.")
        parsed = urlsplit(self.base_url.strip().rstrip("/"))
        host = (parsed.hostname or "").casefold()
        try:
            port = parsed.port
            ip_address(host)
            ip_host = True
        except ValueError:
            ip_host = False
            try:
                port = parsed.port
            except ValueError as exc:
                raise ConfluenceConfigurationError("Confluence site URL has an invalid port.") from exc
        if (
            parsed.scheme != "https"
            or ip_host
            or (host != "atlassian.net" and not host.endswith(".atlassian.net"))
            or parsed.username
            or parsed.password
            or port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ConfluenceConfigurationError(
                "Confluence must use an HTTPS Atlassian Cloud site URL without credentials or extra URL parts."
            )
        spaces = self.normalized_space_keys
        if not spaces or len(spaces) > 20:
            raise ConfluenceConfigurationError("Select between 1 and 20 Confluence space keys.")
        if any(not _SPACE_KEY.fullmatch(key) for key in spaces):
            raise ConfluenceConfigurationError("Confluence space keys must use uppercase letters, digits, and underscores.")

    @property
    def normalized_space_keys(self) -> tuple[str, ...]:
        return tuple(sorted({key.strip().upper() for key in self.space_keys if key.strip()}))

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.strip().rstrip("/")

    @property
    def consent_scope(self) -> str:
        value = f"{self.normalized_base_url}\0{','.join(self.normalized_space_keys)}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @property
    def credential_scope(self) -> str:
        value = f"{self.normalized_base_url}\0{self.email.strip().casefold()}\0{','.join(self.normalized_space_keys)}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def minimized_query_terms(query: str) -> list[str]:
    """Keep a short set of topical words; drop contact and identifier patterns."""
    without_identifiers = _EMAIL.sub(" ", _MIXED_IDENTIFIER.sub(" ", _UUID.sub(" ", query)))
    terms: list[str] = []
    seen: set[str] = set()
    for value in _WORD.findall(without_identifiers):
        term = value.casefold()
        if term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return terms


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style"}:
            self.hidden_depth += 1
        elif tag.casefold() in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag.casefold() in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value[:MAX_BODY_CHARS * 4])
    return " ".join(" ".join(parser.parts).split())[:MAX_BODY_CHARS]


class ConfluenceAdapter:
    """A read-only Cloud client that never follows provider-supplied URLs."""

    provider_id = "confluence-cloud"
    capabilities = frozenset({"connection_check", "search", "versioned_evidence"})

    def __init__(self, config: ConfluenceConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        config.validate()
        self.config = config
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.config.normalized_base_url}/wiki/rest/api/",
            auth=(self.config.email.strip(), self.config.api_token.strip()),
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(8.0, connect=3.0),
            follow_redirects=False,
            transport=self._transport,
        )

    def _space_clause(self) -> str:
        spaces = ", ".join(f'"{key}"' for key in self.config.normalized_space_keys)
        return f"space IN ({spaces})"

    async def check_connection(self) -> dict[str, object]:
        """Check credentials and selected-space access without sending ticket text."""
        cql = f"type=page AND status=current AND {self._space_clause()}"
        async with self._client() as client:
            payload = await self._get_json(client, "search", params={"cql": cql, "limit": 1})
        results = payload.get("results")
        if not isinstance(results, list):
            raise ConfluenceRequestError("Confluence returned an invalid search response.")
        return {"site_reachable": True, "result_count": len(results)}

    async def search(self, ticket_text: str) -> list[dict[str, str]]:
        terms = minimized_query_terms(ticket_text)
        if not terms:
            return []
        text_clause = " OR ".join(f'text ~ "{term}"' for term in terms)
        cql = f"type=page AND status=current AND {self._space_clause()} AND ({text_clause})"
        async with self._client() as client:
            payload = await self._get_json(client, "search", params={"cql": cql, "limit": MAX_RESULTS})
            results = payload.get("results")
            if not isinstance(results, list):
                raise ConfluenceRequestError("Confluence returned an invalid search response.")
            sources: list[dict[str, str]] = []
            for item in results[:MAX_RESULTS]:
                content = item.get("content") if isinstance(item, dict) else None
                content = content if isinstance(content, dict) else item
                content_id = str(content.get("id", "")) if isinstance(content, dict) else ""
                if not _CONTENT_ID.fullmatch(content_id):
                    continue
                page = await self._get_json(
                    client,
                    f"content/{content_id}",
                    params={"expand": "body.storage,version,space"},
                    max_response_bytes=262_144,
                )
                title = page.get("title")
                content_type = page.get("type")
                status = page.get("status")
                body = page.get("body")
                body = body.get("storage") if isinstance(body, dict) else None
                body_html = body.get("value") if isinstance(body, dict) else None
                version = page.get("version")
                modified = version.get("when") if isinstance(version, dict) else ""
                version_number = version.get("number") if isinstance(version, dict) else ""
                space = page.get("space")
                space_key = space.get("key") if isinstance(space, dict) else ""
                if (
                    not isinstance(title, str)
                    or content_type != "page"
                    or status != "current"
                    or not isinstance(body_html, str)
                    or not isinstance(space_key, str)
                    or space_key.upper() not in self.config.normalized_space_keys
                    or type(version_number) is not int
                    or version_number < 1
                    or not isinstance(modified, str)
                    or not modified
                ):
                    continue
                text = _plain_text(body_html)
                if not text:
                    continue
                sources.append(
                    {
                        "id": f"CONF-{content_id}",
                        "title": html.unescape(title)[:180],
                        "category": "confluence",
                        "body": text,
                        "source_version": str(version_number)[:32],
                        "last_modified": str(modified)[:64],
                        "space_key": space_key.upper(),
                    }
                )
            return sources

    @staticmethod
    async def _get_json(
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, str | int],
        max_response_bytes: int = 1_048_576,
    ) -> dict[str, object]:
        try:
            async with client.stream("GET", path, params=params) as response:
                if response.is_redirect:
                    raise ConfluenceRequestError("Confluence redirected the request; credentials were not forwarded.")
                if response.status_code >= 400:
                    raise ConfluenceRequestError(f"Confluence request failed (HTTP {response.status_code}).")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > max_response_bytes:
                    raise ConfluenceRequestError("Confluence response exceeded the supported size limit.")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_response_bytes:
                        raise ConfluenceRequestError("Confluence response exceeded the supported size limit.")
                    chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ConfluenceRequestError("Confluence request timed out.") from exc
        except httpx.RequestError as exc:
            raise ConfluenceRequestError("Confluence request failed.") from exc
        try:
            payload = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ConfluenceRequestError("Confluence returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise ConfluenceRequestError("Confluence returned an invalid response.")
        return payload


def configured_confluence() -> ConfluenceConfig | None:
    base_url = os.getenv("CONFLUENCE_BASE_URL", "").strip()
    email = os.getenv("CONFLUENCE_USER_EMAIL", "").strip()
    api_token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
    raw_spaces = os.getenv("CONFLUENCE_SPACE_KEYS", "")
    if not any((base_url, email, api_token, raw_spaces)):
        return None
    config = ConfluenceConfig(
        base_url=base_url,
        email=email,
        api_token=api_token,
        space_keys=tuple(value.strip() for value in raw_spaces.split(",") if value.strip()),
    )
    if api_token and os.getenv("CONFLUENCE_API_TOKEN_SCOPE", "").strip() != config.credential_scope:
        raise ConfluenceConfigurationError(
            "The saved Confluence token is bound to a different site or space selection; enter it again."
        )
    config.validate()
    return config
