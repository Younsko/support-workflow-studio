# Connector development guide

This guide describes the current adapter boundary and the requirements for adding a connector. The repository currently ships one live ITSM connector: Jira Service Management Cloud. The visual workflow definition API is local. A fail-closed compiler connects only the exact linear starter topology to the existing ticket runtime; it does not yet dispatch arbitrary graphs or execute connector nodes as independent workers.

## Current contracts

The versioned connector contracts are in `backend/app/connector_sdk.py` (`CONNECTOR_SDK_VERSION = "1.0"`). They are part of this repository's Python package; they are not yet published as a separate SDK distribution. `ITSMAdapter` describes normalized ticket operations, and `KnowledgeSourceAdapter` describes read-only connection checks and evidence search. `ConfluenceAdapter` implements the knowledge contract. Knowledge evidence keeps the existing normalized fields, so a new source does not require changing the workflow document format.

`ITSMConnectorRegistry` builds only explicitly registered, trusted in-process adapters. It rejects duplicate or invalid provider IDs, unknown providers, provider identity mismatches, and capabilities outside the SDK vocabulary. `backend/app/itsm.py` registers Jira Service Management at startup and keeps `create_itsm_adapter()` as the app-facing factory. Connector code is operator-supplied Python; workspaces cannot register or load code, and there is no automatic plugin discovery.

An ITSM implementation declares a stable `provider_id` and only the capabilities it actually implements, then provides:

| Method | Contract |
| --- | --- |
| `list_workspaces()` | Return bounded, operator-visible service desks, projects, or workspaces. |
| `list_queues(workspace_id)` | Return only queues available to the configured identity in the selected workspace. |
| `validate_connection()` | Perform a bounded read and return the number of records checked. Never write a ticket. |
| `list_queue_cases(limit, start)` | Return normalized `CaseRecord` objects and honor bounded pagination. |
| `get_request_details(issue_key)` | Refresh the exact request before any decision or publish action. |
| `publish_public_reply(issue_key, body)` | Publish only an approved reply and confirm the provider's reply ID and public visibility. |

The supported ITSM capability IDs are `workspace_discovery`, `queue_discovery`, `queue_read`, `request_refresh`, and `public_reply`. Keep a connector's credentials out of `CaseRecord`, workflow definitions, user-facing error text, logs, and audit metadata.

## Failure behavior

- Raise `ITSMConfigurationError` when required configuration or permissions are missing.
- Raise `ITSMRequestError` for a provider response known to have failed.
- Raise `ITSMOutcomeUnknown` (or a provider-specific subclass) when a write might have succeeded but its result cannot be confirmed. The caller must inspect the ticket and must not retry blindly.
- Do not retry non-idempotent writes automatically. Bounded retries are appropriate only for safe reads or provider-documented idempotent operations.
- Disable redirects for requests carrying credentials. Use HTTPS for remote providers and reject URL credentials, unexpected ports, query strings, and fragments.
- Return sanitized, bounded errors. Avoid including response bodies, authorization headers, tokens, or full ticket contents.

## How to add an ITSM connector

1. Add a provider-specific configuration type with secret fields excluded from representation and serialization.
2. Implement `ITSMAdapter` and declare the exact capabilities the connector supports. Do not advertise queue reads, refresh, or public replies unless each operation is implemented and verified.
3. Register the provider with an explicit `ITSMConnectorRegistry` instance used by the host application; unknown names must continue to fail closed. Do not load tenant-selected Python modules or arbitrary entry points.
4. Add synthetic HTTP fixtures and contract tests for bounded pagination, malformed responses, unauthorized access, rate limits, stale source data, public/private reply visibility, and uncertain write outcomes.
5. Keep connection discovery read-only. Test least-privilege scopes and document the vendor role needed for each capability.
6. Add setup UI and documentation only after the adapter and its tests pass. Live validation must use a test tenant with synthetic tickets.

## Knowledge and model connectors

Knowledge retrieval supports an operator-selected local folder and a read-only, scope-limited Confluence Cloud adapter. New remote sources should implement `KnowledgeSourceAdapter`, return the existing evidence shape, state their read-only capabilities, and keep source identity/version metadata for review. Confluence search sends up to eight minimized ticket terms only after explicit consent bound to the site and space selection. It caps result and response sizes, uses fixed API paths, rejects redirects, validates server-returned content IDs, rechecks page status and space, and preserves version metadata in evidence. Search terms may still include sensitive words; they are not anonymized. The interface contract does not yet provide hosted secret delivery, tenant isolation, webhook freshness, or a production approval registry. Model providers are adapters in `backend/app/providers.py`, but credentials are local configuration rather than runner-scoped secret references.

## Before a connector is production-ready

- Confirm current vendor documentation, API versions, data residency, rate limits, and least-privilege permissions.
- Define request timeouts, retry rules, pagination limits, duplicate detection, and webhook replay handling.
- Bind approvals to an immutable ticket and draft snapshot. Re-fetch the ticket immediately before external writes.
- Provide reconciliation for timeouts after a write, audit the actor and operation, and prove secrets are redacted from logs.
- Run a synthetic test-tenant pilot and record the tested API behavior and limitations.
