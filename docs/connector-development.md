# Connector development guide

This guide describes the current adapter boundary and the requirements for adding a connector. The repository currently ships one live ITSM connector: Jira Service Management Cloud. The visual workflow definition API is still local, and published definitions do not execute through adapters.

## Current contracts

The ITSM contract is `ITSMAdapter` in `backend/app/itsm.py`. An implementation declares a stable `provider_id` and explicit capabilities, then implements:

| Method | Contract |
| --- | --- |
| `list_workspaces()` | Return bounded, operator-visible service desks, projects, or workspaces. |
| `list_queues(workspace_id)` | Return only queues available to the configured identity in the selected workspace. |
| `validate_connection()` | Perform a bounded read and return the number of records checked. Never write a ticket. |
| `list_queue_cases(limit, start)` | Return normalized `CaseRecord` objects and honor bounded pagination. |
| `get_request_details(issue_key)` | Refresh the exact request before any decision or publish action. |
| `publish_public_reply(issue_key, body)` | Publish only an approved reply and confirm the provider's reply ID and public visibility. |

`create_itsm_adapter()` is the registration point. Unsupported provider IDs must fail closed. Keep a connector's credentials out of `CaseRecord`, workflow definitions, user-facing error text, logs, and audit metadata.

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
3. Add an explicit branch to `create_itsm_adapter()`; unknown names must continue to fail closed.
4. Add synthetic HTTP fixtures and contract tests for bounded pagination, malformed responses, unauthorized access, rate limits, stale source data, public/private reply visibility, and uncertain write outcomes.
5. Keep connection discovery read-only. Test least-privilege scopes and document the vendor role needed for each capability.
6. Add setup UI and documentation only after the adapter and its tests pass. Live validation must use a test tenant with synthetic tickets.

## Knowledge and model connectors

Knowledge retrieval currently uses the selected local folder and its approved-source manifest; there is not yet a shared remote knowledge connector protocol. Model providers are adapters in `backend/app/providers.py`, but credentials are local configuration rather than runner-scoped secret references. These are separate contracts today. Do not claim Confluence, ServiceNow, arbitrary knowledge sources, or tenant-safe credential delivery until each path is implemented and tested.

## Before a connector is production-ready

- Confirm current vendor documentation, API versions, data residency, rate limits, and least-privilege permissions.
- Define request timeouts, retry rules, pagination limits, duplicate detection, and webhook replay handling.
- Bind approvals to an immutable ticket and draft snapshot. Re-fetch the ticket immediately before external writes.
- Provide reconciliation for timeouts after a write, audit the actor and operation, and prove secrets are redacted from logs.
- Run a synthetic test-tenant pilot and record the tested API behavior and limitations.
