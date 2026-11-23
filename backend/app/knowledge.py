from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

from .confluence import (
    ConfluenceAdapter,
    ConfluenceConfigurationError,
    ConfluenceRequestError,
    configured_confluence,
)

MAX_FILES = 200
MAX_FILE_BYTES = 256_000
MAX_TOTAL_BYTES = 5_000_000
MAX_SOURCE_CHARS = 12_000
APPROVAL_MANIFEST = ".support-workflow-approved-sources.json"
SEARCH_STOPWORDS = {
    "about", "after", "also", "and", "are", "can", "could", "did", "does",
    "for", "from", "had", "has", "have", "help", "how", "into", "its",
    "just", "more", "not", "our", "please", "request", "reported", "still",
    "such", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "under", "use", "using", "very", "was",
    "were", "what", "when", "where", "which", "while", "who", "will", "with",
    "would", "your", "service", "system", "issue", "device",
}

KNOWLEDGE = [
    {
        "id": "KB-104",
        "title": "VPN connection checks",
        "category": "connectivity",
        "body": (
            "For a VPN connection issue, confirm the device has internet access, "
            "capture the exact error and timestamp, and check the approved VPN "
            "service status page. Do not ask the requester to share a password, "
            "one-time code, or private key. Escalate repeated failures with the "
            "error text and affected device platform."
        ),
    },
    {
        "id": "KB-221",
        "title": "Application crash evidence checklist",
        "category": "application",
        "body": (
            "For an application crash, record the app version, device platform, "
            "approximate time, whether the issue repeats, and any visible error. "
            "Ask whether work is blocked and whether another approved route is "
            "available. Avoid requesting full logs that may contain personal or "
            "confidential data; collect only the minimum relevant excerpt."
        ),
    },
    {
        "id": "KB-308",
        "title": "Account sign-in triage",
        "category": "identity",
        "body": (
            "For sign-in problems, confirm the application, approximate time, "
            "and exact error wording. Never request passwords or recovery codes. "
            "If multiple people are affected or a security alert is reported, "
            "route to the identity on-call team for review."
        ),
    },
    {
        "id": "KB-410",
        "title": "Password reset self-service",
        "category": "identity",
        "body": (
            "For a routine password reset when the requester can still sign in, "
            "direct them to the organization's approved self-service account "
            "portal and its password-reset flow. Do not invent or send a portal "
            "URL. Never ask for the current or new password, recovery code, or "
            "one-time code. If the approved flow is unavailable or returns an "
            "error, collect the exact error wording without secrets and route to "
            "the identity support team."
        ),
    },
]


class KnowledgeConfigurationError(RuntimeError):
    pass


class KnowledgeRequestError(RuntimeError):
    pass


def _configured_directory() -> str:
    return os.getenv("KNOWLEDGE_DIR", "").strip()


def _approved_source_paths(root: Path) -> list[PurePosixPath]:
    manifest_path = root / APPROVAL_MANIFEST
    try:
        resolved_manifest = manifest_path.resolve(strict=True)
        if not resolved_manifest.is_relative_to(root) or not resolved_manifest.is_file():
            raise KnowledgeConfigurationError(
                f"KNOWLEDGE_DIR requires a valid {APPROVAL_MANIFEST} allowlist."
            )
        if resolved_manifest.stat().st_size > MAX_FILE_BYTES:
            raise KnowledgeConfigurationError(
                f"{APPROVAL_MANIFEST} exceeds the supported size limit."
            )
        manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except KnowledgeConfigurationError:
        raise
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeConfigurationError(
            f"KNOWLEDGE_DIR requires a valid {APPROVAL_MANIFEST} allowlist."
        ) from exc

    approved = manifest.get("approved_sources") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("version")) is not int
        or manifest.get("version") != 1
        or not isinstance(approved, list)
        or not approved
        or len(approved) > MAX_FILES
    ):
        raise KnowledgeConfigurationError(
            f"{APPROVAL_MANIFEST} must list 1–{MAX_FILES} approved relative source paths."
        )

    paths: list[PurePosixPath] = []
    seen: set[str] = set()
    for value in approved:
        if not isinstance(value, dict):
            raise KnowledgeConfigurationError(
                f"{APPROVAL_MANIFEST} contains an invalid approval record."
            )
        source_path = value.get("path")
        approval_fields = (
            value.get("approval_status") == "approved",
            isinstance(value.get("owner_ref"), str)
            and bool(value["owner_ref"].strip()),
            isinstance(value.get("approved_at"), str)
            and bool(value["approved_at"].strip()),
            isinstance(value.get("approval_evidence_ref"), str)
            and bool(value["approval_evidence_ref"].strip()),
        )
        if not all(approval_fields):
            raise KnowledgeConfigurationError(
                f"{APPROVAL_MANIFEST} contains a source without complete approval metadata."
            )
        if not isinstance(source_path, str) or not source_path.strip() or "\\" in source_path:
            raise KnowledgeConfigurationError(
                f"{APPROVAL_MANIFEST} contains an invalid source path."
            )
        relative = PurePosixPath(source_path)
        if (
            relative.is_absolute()
            or any(part in {"", ".", ".."} or part.startswith(".") for part in relative.parts)
            or relative.suffix.lower() not in {".md", ".txt"}
            or source_path in seen
        ):
            raise KnowledgeConfigurationError(
                f"{APPROVAL_MANIFEST} contains an unsafe or duplicate source path."
            )
        seen.add(source_path)
        paths.append(relative)
    return paths


def _read_local_sources(directory: str) -> list[dict[str, str]]:
    try:
        root = Path(directory).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise KnowledgeConfigurationError(
            "KNOWLEDGE_DIR must point to an existing local directory."
        ) from exc
    if not root.is_dir():
        raise KnowledgeConfigurationError(
            "KNOWLEDGE_DIR must point to an existing local directory."
        )

    sources: list[dict[str, str]] = []
    total_bytes = 0
    for relative in _approved_source_paths(root):
        if len(sources) >= MAX_FILES or total_bytes >= MAX_TOTAL_BYTES:
            break
        path = root.joinpath(*relative.parts)
        if not path.exists():
            raise KnowledgeConfigurationError(
                f"{APPROVAL_MANIFEST} references a missing or out-of-folder source."
            )
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise KnowledgeConfigurationError(
                    f"{APPROVAL_MANIFEST} references a missing or out-of-folder source."
                )
            size = resolved.stat().st_size
            if (
                size == 0
                or size > MAX_FILE_BYTES
                or total_bytes + size > MAX_TOTAL_BYTES
            ):
                continue
            content = resolved.read_text(encoding="utf-8", errors="replace").strip()
        except KnowledgeConfigurationError:
            raise
        except (OSError, RuntimeError):
            continue
        if not content:
            continue

        relative_path = path.relative_to(root).as_posix()
        match = re.search(r"^\s*#\s+(.+?)\s*$", content, re.MULTILINE)
        title = (match.group(1) if match else path.stem).strip()[:120]
        if len(content) > MAX_SOURCE_CHARS:
            title = f"{title} (excerpt)"
        source_id = (
            hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:10].upper()
        )
        sources.append(
            {
                "id": f"LOCAL-{source_id}",
                "title": title,
                "category": "local-knowledge",
                "body": content[:MAX_SOURCE_CHARS],
            }
        )
        total_bytes += size
    return sources


def knowledge_status() -> dict[str, object]:
    provider = os.getenv("KNOWLEDGE_PROVIDER", "local").strip().lower() or "local"
    if provider == "confluence":
        if os.getenv("PRIVATE_LOCAL_MODE", "false").strip().lower() in {"1", "true", "yes"}:
            return {
                "mode": "confluence",
                "configured": True,
                "available": False,
                "source_count": 0,
                "message": "Confluence is disabled while PRIVATE_LOCAL_MODE is active.",
            }
        try:
            config = configured_confluence()
        except ConfluenceConfigurationError as exc:
            return {
                "mode": "confluence",
                "configured": False,
                "available": False,
                "source_count": 0,
                "message": str(exc),
            }
        if config is None:
            return {
                "mode": "confluence",
                "configured": False,
                "available": False,
                "source_count": 0,
                "message": "Configure a Confluence Cloud site, account, token, and space allowlist.",
            }
        consented = (
            os.getenv("CONFLUENCE_TICKET_SEARCH_ALLOWED", "false").strip().lower()
            in {"1", "true", "yes"}
            and os.getenv("CONFLUENCE_SEARCH_CONSENT_SCOPE", "").strip()
            == config.consent_scope
        )
        return {
            "mode": "confluence",
            "configured": True,
            "available": consented,
            "source_count": 0,
            "message": (
                "Ticket search terms are approved for the selected Confluence site and spaces; the connection is checked when requested."
                if consented
                else "Confirm that minimized ticket search terms may be sent to the selected Confluence site and spaces."
            ),
        }
    if provider not in {"local", "synthetic"}:
        return {
            "mode": provider,
            "configured": False,
            "available": False,
            "source_count": 0,
            "message": "The selected knowledge provider is unsupported.",
        }
    directory = _configured_directory()
    if not directory:
        return {
            "mode": "synthetic",
            "configured": False,
            "available": True,
            "source_count": len(KNOWLEDGE),
            "message": "Synthetic demo knowledge is active.",
        }
    try:
        sources = _read_local_sources(directory)
    except KnowledgeConfigurationError as exc:
        return {
            "mode": "local",
            "configured": True,
            "available": False,
            "source_count": 0,
            "message": str(exc),
        }
    return {
        "mode": "local",
        "configured": True,
        "available": bool(sources),
        "source_count": len(sources),
        "message": (
            "Allowlisted operator-provided local knowledge is active."
            if sources
            else "The approved source allowlist has no readable .md or .txt files."
        ),
    }


def retrieve(query: str) -> list[dict[str, str]]:
    directory = _configured_directory()
    sources = _read_local_sources(directory) if directory else KNOWLEDGE
    tokens = {
        token
        for token in re.findall(r"[\w-]{3,}", query.lower())
        if token not in SEARCH_STOPWORDS
    }

    def relevance(article: dict[str, str]) -> int:
        article_tokens = set(
            re.findall(
                r"[\w-]{3,}",
                f"{article['title']} {article['category']} {article['body']}".lower(),
            )
        )
        return len(tokens & article_tokens)

    ranked = sorted(sources, key=relevance, reverse=True)
    best = relevance(ranked[0]) if ranked else 0
    minimum = max(1, (best * 3 + 3) // 4)
    return [dict(item) for item in ranked[:2] if relevance(item) >= minimum]


async def retrieve_configured(query: str) -> list[dict[str, str]]:
    """Retrieve from the selected source, with explicit scope-bound remote consent."""
    provider = os.getenv("KNOWLEDGE_PROVIDER", "local").strip().lower() or "local"
    if provider != "confluence":
        return retrieve(query)
    if os.getenv("PRIVATE_LOCAL_MODE", "false").strip().lower() in {"1", "true", "yes"}:
        raise KnowledgeConfigurationError(
            "Confluence retrieval is disabled while PRIVATE_LOCAL_MODE is active."
        )
    try:
        config = configured_confluence()
    except ConfluenceConfigurationError as exc:
        raise KnowledgeConfigurationError(str(exc)) from exc
    if config is None:
        raise KnowledgeConfigurationError("Confluence knowledge is not configured.")
    consented = (
        os.getenv("CONFLUENCE_TICKET_SEARCH_ALLOWED", "false").strip().lower()
        in {"1", "true", "yes"}
        and os.getenv("CONFLUENCE_SEARCH_CONSENT_SCOPE", "").strip()
        == config.consent_scope
    )
    if not consented:
        raise KnowledgeConfigurationError(
            "Confluence ticket-search consent is missing for the current site and space selection."
        )
    try:
        return await ConfluenceAdapter(config).search(query)
    except ConfluenceRequestError as exc:
        raise KnowledgeRequestError(str(exc)) from exc
