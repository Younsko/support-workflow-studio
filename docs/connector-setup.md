# Connector and workspace setup

Support Workflow Studio treats an ITSM system as a source of support requests and, where implemented, a destination for a human-approved reply. A connector must map its provider's records into the shared `CaseRecord` shape. It must preserve the upstream ticket key, source identity, timestamps, requester text, priority, impact and state, while keeping raw untrusted text distinguishable from verified evidence.

## Connector contract

`backend/app/itsm.py` defines the `ITSMAdapter` protocol, and the app routes use it for queue reads, request refreshes, connection checks, and public replies. An adapter owns provider-specific configuration and API details. It implements:

- `validate_connection` — confirm credentials and access without changing a ticket.
- `list_queue_cases` — return a bounded page of normalized open requests.
- `get_request_details` — refresh the exact source ticket before any write.
- `publish_public_reply` — post only the already human-approved response and report a confirmed comment ID or an unknown outcome.

`JiraServiceManagementAdapter` is the only implementation currently registered by `create_itsm_adapter`. That factory rejects every unimplemented provider ID. The adapter advertises `queue_read`, `request_refresh` and `public_reply`; the integration-status endpoint reports these capabilities only when Jira is configured and external ITSM use is enabled.

Each adapter must validate HTTPS endpoints and resource identifiers, set request timeouts, bound pagination and response sizes, treat requester content as untrusted, and avoid logging credentials or ticket bodies. Jira reads retry HTTP 429 responses at most twice only when `Retry-After` is valid and the requested wait is no more than five seconds; longer or missing retry instructions are returned to the caller without an early retry, following Atlassian's [Jira Cloud rate-limit guidance](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/). Writes are never automatically retried: a timed-out or ambiguous write must raise the shared `ITSMOutcomeUnknown` error and be checked in the ITSM before any retry. Connection and read errors should use `ITSMConfigurationError` or `ITSMRequestError` so the common API layer can handle provider failures consistently.

Provider capabilities are explicit. Queue reads and public replies are separate. Connectors must declare whether they can read queues, refresh ticket snapshots, and publish comments. The UI must not show a capability as available until the adapter has a working implementation.

## Current support

Jira Service Management Cloud is the first and only live ITSM adapter. Open **Setup** in the app to enter its site URL, account email and API token, then use **Find service desks** and select a service desk and queue. Discovery is read-only and keeps the submitted token in memory only; final save checks access to the chosen queue before persisting settings. The token is saved only by the local backend in the ignored `.env` file; the setup API never returns it. Service-desk discovery uses Jira's [Get service desks](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/) operation; queue discovery uses the same API group and requires service-desk agent access. Manual numeric IDs remain available as a fallback. Use **Sync Jira queue** to refresh the workbench. Public comments require approval of the exact draft and a separate publish action. The publisher refreshes the ticket and rejects changed source content.

Setup also accepts an operator-selected local knowledge directory and checks that its approved-source manifest is usable. The UI exposes the auto-triage opt-in, polling interval, per-poll limit and phrase rules for N1 and N3/N3+ provisional routing. Operator phrases add to built-in routing signals, including baseline security and broad-impact cues; tickets that do not match default to N2 investigation. Automatic triage creates drafts only. The same local setup panel can select demo, OpenAI-compatible, Anthropic, or loopback Ollama model settings; it validates the configuration without sending a prompt. External model use requires consent for the selected provider and endpoint; changing either requires fresh consent and a key scoped to that destination. Model keys are saved to the local ignored `.env` file and never read back to the UI. The workflow canvas displays these local connection states and fills the corresponding reference IDs, but it still has no execution runner or tenant-scoped connection registry. It labels remote model settings as saved but untested because setup does not verify model reachability. Other ITSM integrations, distributed OAuth installation, a runner-owned managed secret store, and hosted model credential delivery are not implemented; do not treat another provider as available until its adapter is built and verified.

## Knowledge sources

Jira triage requires an operator-selected local directory containing approved Markdown or text files and the `.support-workflow-approved-sources.json` allowlist. The application reads only listed files, with limits on file count and size. Choose the directory in **Setup**. Knowledge sources are used as evidence, not as permission to perform an external action.

## Extend the adapter

Implement the connector behind the same normalized provider interface. Add contract tests for connection validation, pagination, field mapping, source snapshot changes, API failure, timeout, duplicate delivery and public-write outcomes. Keep provider credentials in a server-side secret store. Do not add secrets or tenant data to the public repository, browser storage, screenshots, logs, synthetic fixtures or model prompts.

An operator interface for installing and configuring adapters needs to pair provider-specific auth with a local secret store, capability discovery and the same read/write approval boundaries. A generic URL-and-token form alone cannot safely configure unrelated ITSM APIs.
