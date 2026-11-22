# Data boundaries

This public repository contains application code, generic architecture notes, and synthetic requests and guidance. Do not add real support records, credentials, private identifiers, or confidential runbooks to commits, issues, releases, screenshots, or evaluation reports.

## Public examples

Author new examples as synthetic cases. Removing a few fields from a real ticket does not make it a safe public fixture. Keep local `.env` files, databases, API tokens, exports, and provider credentials out of Git.

## Local use

The operator selects a knowledge directory and an explicit allowlist of approved guidance files. The app checks the manifest shape, but cannot verify organizational approval. For confidential cases, use a separate local copy, minimize the request text, verify the selected inference provider and its data handling, and obtain the necessary authorization before connecting a ticket source.

`PRIVATE_LOCAL_MODE=true` requires local Ollama inference and blocks Jira reads and writes. It is a prototype guard, not an access-control system. The default public demo uses synthetic data and makes no model calls.

## Release check

Before publishing a change, inspect staged files and Git history, scan for secrets and real identifiers, and run the test suite. The ignored `.local/` directory contains local workflow state and must never be committed.
