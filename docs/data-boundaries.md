# Data boundaries

This public repository contains application code, generic architecture notes, and synthetic requests and guidance. Do not add real support records, credentials, private identifiers, or confidential runbooks to commits, issues, releases, screenshots, or evaluation reports.

## Public examples

Author new examples as synthetic cases. Removing a few fields from a real ticket does not make it a safe public fixture. Keep local `.env` files, databases, API tokens, exports, and provider credentials out of Git.

## Local use

The operator selects either a local knowledge directory with an explicit allowlist or a read-only Confluence Cloud site and space allowlist. The app checks the local manifest shape, but cannot verify organizational approval; a Confluence space selection is an operator trust boundary and does not establish that every page is approved. Confluence receives up to eight minimized search terms derived from the ticket, not the full ticket body; the terms may still contain names or sensitive words and are not anonymized. The connection check is user-triggered, sends no ticket content, and does not run on Setup save. Retrieved page content is untrusted evidence. The selected model receives the ticket and evidence needed for its run under the separately configured model consent.

Confluence credentials stay in the local ignored `.env` file in this prototype; in the Compose preview, Setup writes them to `settings.env` inside its named local Docker volume. This does not provide hosted runner secret delivery, tenant isolation, or encrypted browser-to-runner handling. For confidential cases, use a separate local copy, review the selected spaces and provider data handling, minimize the request text, and obtain the necessary authorization before connecting ticket sources.

`PRIVATE_LOCAL_MODE=true` requires local Ollama inference and blocks Jira reads and writes. It also rejects `SUPPORT_WORKFLOW_DATABASE_URL`, because this prototype cannot verify that a Postgres host is local; use its default SQLite store in this mode. It is a prototype guard, not an access-control system. The default public demo uses synthetic data and makes no model calls.

## Release check

Before publishing a change, inspect staged files and Git history, scan for secrets and real identifiers, and run the test suite. The ignored `.local/` directory contains local workflow state and must never be committed.
