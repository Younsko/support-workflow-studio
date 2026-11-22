# Security threat model

## Scope and status

This model covers the current single-operator local prototype and records the additional controls required before a hosted team service or customer runner can be called production-ready. It is an engineering baseline, not an independent penetration test or a certification.

The prototype has no accounts, workspace membership, tenant isolation, remote runner, or encrypted collaboration channel. Its workflow-definition endpoints are intended for loopback use. Do not expose this server to a network or use real customer ticket content with the visual workflow-definition API.

## Assets

| Asset | Why it matters | Current storage or recipient |
|---|---|---|
| Ticket text, requester details, and replies | May include personal, operational, or confidential information | Local app memory and SQLite; Jira when configured; a selected model provider when live inference is enabled |
| Approved knowledge sources | May contain internal procedures | Local files selected by the approved-source manifest |
| Connector and model credentials | Allow access to external systems or incur usage | Environment configuration in the local prototype |
| Workflow definitions and published versions | Determine which data is read and which actions can be requested | Local SQLite |
| Human decisions and external writes | Can authorize a customer-visible reply | Local runtime state and Jira; workflow-definition audit records contain metadata only |
| Run history and logs | Can reveal ticket content or decision context | Local SQLite and application process |

## Trust boundaries

1. **Browser to local API:** The prototype has no user authentication. The API middleware restricts accepted hostnames and client addresses to loopback, and checks cross-origin write requests. CORS permits the local development origins and the editor's required methods.
2. **Application to local disk:** SQLite stores workflow definitions and the existing ticket runtime state. Local file permissions, backup handling, and host compromise remain the operator's responsibility. Workflow audit events are not tamper-evident and do not identify authenticated actors.
3. **Application to external services:** Jira and model providers receive data when those existing integrations are configured. Provider choice and external-processing consent must be reviewed before using live ticket data. Local Ollama is supported by the existing runtime; no hosted runner credential relay is implemented.
4. **Untrusted ticket and knowledge content:** Both can contain misleading instructions or malicious prompt-injection text. Content must be treated as data. The workflow-definition schema limits configured agent tools to read-only identifiers, but the new visual definitions do not execute yet; this is not proof that the separate ticket runtime is immune to prompt injection.
5. **Human review to external publication:** The current ticket runtime separates a human decision from the explicit Jira reply action. The visual workflow validator also requires a human-approval node on every path to a publish node. These checks are local prototype controls, not workspace-role authorization.

## Threats and controls

| Threat | Current control | Remaining requirement before production |
|---|---|---|
| Remote caller reaches unauthenticated local APIs | Loopback hostname and client-address checks; restricted CORS | Bind explicitly to loopback by default, add deployment tests, then enforce authenticated workspace authorization in any hosted mode |
| Stale browser tab overwrites a newer workflow draft | Revision check on save/publish; fork conflict returns HTTP 409 with a stable error code and current revision | Add multi-client concurrency tests against the production database and expose conflict recovery in every client |
| Published version changes after review | Immutable serialized version and content hash; edits start a new draft | Sign or otherwise protect release provenance as the deployment model requires; retain immutable version history under tenant authorization |
| Workflow smuggles credentials or arbitrary tools | Per-node config allowlists, sensitive configuration-key rejection, explicit read-only agent-tool allowlist, finite JSON validation | Add secret-value scanning and managed secret references; independently review the complete runner tool dispatch before execution is connected |
| Publish is reachable without human review | Graph validator checks that every route to `itsm.publish` crosses an approval node; only `public_reply` is accepted | Enforce approval identity, role, exact ticket/version snapshot, expiry, and source freshness in the runner; fail closed on unknown publish outcomes |
| Ticket or knowledge text overrides agent instructions | Treated as untrusted in the intended design; tool list is constrained in the workflow-definition contract | Add adversarial prompt-injection tests to the actual execution path; isolate data from instructions; prohibit write-capable tools before explicit approval |
| Credential appears in workflow configuration or logs | Secret-like config keys are rejected; current audit events store metadata and hashes rather than workflow payloads | Move credentials out of environment-only setup into a runner-owned secret store; test redaction across errors, traces, telemetry, and crash reports |
| Local audit records are edited or deleted | Metadata-only events provide basic local traceability | Use an append-only or externally anchored audit design, authenticated actor IDs, retention policy, and deletion controls appropriate to the hosted threat model |
| Cross-workspace data is disclosed | Not applicable yet; there are no workspaces | Add tenant IDs to every persistent object, authorization checks on every read/write, and cross-tenant negative tests before inviting teams |
| Runner or database restarts duplicate an external write | Existing ticket flow has a separate publish action; workflow definitions are not executed | Add durable leases, idempotency records, an outbox, unknown-outcome reconciliation, restart/backup/restore tests, and connector-specific idempotency behavior |
| Hosted operator reads private ticket content | No hosted control plane or relay exists | Review key creation, recovery, revocation, and rotation; use a reviewed end-to-end encryption protocol and independently verify control-plane non-decryption |

## Production release gates

- No service is reachable outside its declared deployment boundary without authentication and authorization.
- Workspace isolation is tested for every endpoint, role, background job, export, and audit query.
- Secrets never appear in workflow definitions, hosted metadata, browser telemetry, or logs. Rotation and revocation are tested.
- Adversarial ticket and knowledge fixtures cannot authorize new tools, alter policy, or bypass verification and human approval.
- Approval is bound to the exact source ticket and immutable draft shown to the approver; stale content invalidates approval.
- Connector write attempts are idempotent or enter explicit reconciliation when the result is unknown.
- Backups can be restored and runner restarts recover without losing approval state or duplicating a public write.
- Privacy, retention, deletion, incident response, and recovery statements match measured implementation behavior.

## Safe use of the current prototype

- Use synthetic tickets for demonstrations and workflow-definition development.
- Keep the process local and the database directory access-controlled.
- Review model-provider mode and external-processing consent before sending any ticket to a live model.
- Keep external writes disabled until a human has reviewed the exact reply and separately chosen to publish it.
- Treat the current workflow canvas as an editor for local definitions; it is not a workflow executor or a secure team workspace.
