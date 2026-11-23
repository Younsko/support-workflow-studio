# Security threat model

## Scope and status

This model covers the current single-operator local prototype and records the additional controls required before a hosted team service or customer runner can be called production-ready. It is an engineering baseline, not an independent penetration test or a certification.

The prototype has no accounts, workspace membership, tenant isolation, remote runner, or encrypted collaboration channel. Its workflow-definition endpoints are intended for loopback use. Do not expose this server to a network or use real customer ticket content with the visual workflow-definition API.

## Assets

| Asset | Why it matters | Current storage or recipient |
|---|---|---|
| Ticket text, requester details, and replies | May include personal, operational, or confidential information | Local app memory and SQLite; Jira when configured; a selected model provider when live inference is enabled |
| Approved knowledge sources | May contain internal procedures | Local files selected by the approved-source manifest |
| Connector and model credentials | Allow access to external systems or incur usage | Jira and configured model API keys are stored in the local ignored `.env` file; legacy model credentials may also be supplied through local environment configuration |
| Workflow definitions and published versions | Determine which data is read and which actions can be requested | Local SQLite |
| Human decisions and external writes | Can authorize a customer-visible reply | Local runtime state and Jira; workflow-definition audit records contain metadata only |
| Run history and logs | Can reveal ticket content or decision context | Local SQLite and application process |

## Trust boundaries

1. **Browser to local API:** The prototype has no user authentication. By default, API middleware restricts accepted hostnames and client addresses to loopback and checks cross-origin write requests. Compose binds the app port to host loopback and allows only the exact gateway address on its dedicated Docker subnet, while still requiring a loopback Host name. CORS permits local development origins and the editor's required methods. This is a single-host convenience boundary, not an authenticated network deployment.
2. **Application to local disk:** SQLite stores workflow definitions and runtime state. The Setup panel stores Jira and model API keys in the ignored local `.env` file or, in Compose, `settings.env` in the named local volume; the setup API does not return keys to the browser. Model API keys and external-processing consent require matching provider-and-endpoint scopes at runtime; unscoped legacy entries fail closed until re-entered and confirmed in Setup. Setup validates configuration without sending a model request, so its saved status does not prove provider reachability or model availability. The repository does not provide a managed secret vault or Windows ACL hardening. Local file permissions, backup handling, Docker volume access, and host compromise remain the operator's responsibility. Workflow audit events are not tamper-evident and do not identify authenticated actors.
3. **Application to external services:** Jira and model providers receive data when those existing integrations are configured. Provider choice and external-processing consent must be reviewed before using live ticket data. Local Ollama is supported by the existing runtime; no hosted runner credential relay is implemented.
4. **Untrusted ticket and knowledge content:** Both can contain misleading instructions or malicious prompt-injection text. Content must be treated as data. The workflow-definition schema limits configured agent tools to read-only identifiers; the executable compiler currently allows only approved knowledge search. Operator-configured agent instructions are bounded and subordinate to the drafting system constraints, but adversarial testing of the complete executable path remains a release gate.
5. **Human review to external publication:** The current ticket runtime separates a human decision from the explicit Jira reply action. The visual workflow validator also requires a human-approval node on every path to a publish node. These checks are local prototype controls, not workspace-role authorization.

## Threats and controls

| Threat | Current control | Remaining requirement before production |
|---|---|---|
| Remote caller reaches unauthenticated local APIs | Loopback hostname and client-address checks; restricted CORS | Bind explicitly to loopback by default, add deployment tests, then enforce authenticated workspace authorization in any hosted mode |
| Stale browser tab overwrites a newer workflow draft | Revision check on save/publish; fork conflict returns HTTP 409 with a stable error code and current revision | Add multi-client concurrency tests against the production database and expose conflict recovery in every client |
| Published version changes after review | Immutable serialized version and content hash; edits start a new draft | Sign or otherwise protect release provenance as the deployment model requires; retain immutable version history under tenant authorization |
| Workflow smuggles credentials or arbitrary tools | Per-node config allowlists, sensitive configuration-key rejection, explicit read-only agent-tool allowlist, finite JSON validation, and a compiler that rejects all tools except approved knowledge search | Add secret-value scanning and managed secret references; independently review the complete runner tool dispatch before expanding execution beyond the starter graph |
| Publish is reachable without human review or uses a stale ticket snapshot | Graph validator checks that every route to `itsm.publish` crosses an approval node; only `public_reply` is accepted; local decision and publish endpoints refresh Jira summary, full normalized description, and status and compare a full-source fingerprint with the run's reviewed snapshot. UI/model text remains bounded to 4,000 description characters. | Jira comment creation is not conditional on the version just read, so a ticket edit can still race the final freshness check. Add a Jira-supported conditional write if available; otherwise preserve the race as a documented residual risk. Enforce approval identity, role, exact immutable ticket/draft version, and expiry in the runner; add concurrency tests and fail closed on unknown publish outcomes. |
| Ticket or knowledge text overrides agent instructions | Treated as untrusted in the intended design; tool list is constrained in the workflow-definition contract | Add adversarial prompt-injection tests to the actual execution path; isolate data from instructions; prohibit write-capable tools before explicit approval |
| Credential appears in workflow configuration or logs | Secret-like workflow config keys are rejected; current audit events store metadata and hashes rather than workflow payloads; the Setup API does not return saved Jira or model keys | Move credentials out of local `.env`/environment setup into a runner-owned secret store; test redaction across request errors, traces, telemetry, and crash reports |
| Ticket content is sent to an unapproved secondary model | Primary model keys and consent are destination-scoped; optional council and judge profiles also require their own destination-scoped keys and explicit per-profile consent; the model-call boundary rejects unapproved external destinations | Add profile-specific UI, audit attribution for the receiving provider, and adversarial tests before enabling multiple provider destinations for a hosted workspace |
| Local audit records are edited or deleted | Metadata-only events provide basic local traceability | Use an append-only or externally anchored audit design, authenticated actor IDs, retention policy, and deletion controls appropriate to the hosted threat model |
| Workspace session token is replayed or survives a membership change | The control plane can issue a runner-ID-bound RS256 token only after checking current membership and an active runner; lifetime is capped at three minutes and the upstream OIDC token expiry | The support runner does not yet validate these tokens. Add per-route authorization, immutable runner/workspace binding, actor-attributed decisions, a fresh authorization check before publication, and tests for expiry, revocation, rotation, and replay boundaries |
| Cross-workspace data is disclosed | Not applicable yet; there are no workspaces | Add tenant IDs to every persistent object, authorization checks on every read/write, and cross-tenant negative tests before inviting teams |
| Runner or database restarts lose a ticket or duplicate an external write | Jira monitor seen-ticket, baseline, and retry checkpoints persist in SQLite; optional Postgres runner storage adds durable claims, stage journals, and a transactional metadata outbox with `SKIP LOCKED` leasing; explicit reply publication remains separate | Add tenant scoping, a production dispatcher, resumable steps, unknown-outcome reconciliation, Postgres backup/restore drills, and connector-specific idempotency before multi-worker or hosted operation. The Jira monitor and in-memory API caches remain single-process |
| Hosted operator reads private ticket content | No hosted control plane or relay exists | Review key creation, recovery, revocation, and rotation; use a reviewed end-to-end encryption protocol and independently verify control-plane non-decryption |

## Production release gates

- No service is reachable outside its declared deployment boundary without authentication and authorization.
- Workspace isolation is tested for every endpoint, role, background job, export, and audit query.
- Secrets never appear in workflow definitions, hosted metadata, browser telemetry, or logs. Rotation and revocation are tested.
- Adversarial ticket and knowledge fixtures cannot authorize new tools, alter policy, or bypass verification and human approval.
- Approval is bound to the run's source ticket snapshot and current draft version; stale Jira content blocks approval, edit review, and publishing. Hosted versions must bind authenticated approver identity to an immutable source/draft snapshot.
- Connector write attempts are idempotent or enter explicit reconciliation when the result is unknown.
- Backups can be restored and runner restarts recover without losing approval state or duplicating a public write.
- Privacy, retention, deletion, incident response, and recovery statements match measured implementation behavior.

## Safe use of the current prototype

- Use synthetic tickets for demonstrations and workflow-definition development.
- Keep the process local and the database directory access-controlled.
- Review model-provider mode and external-processing consent before sending any ticket to a live model.
- Keep external writes disabled until a human has reviewed the exact reply and separately chosen to publish it.
- Treat the current workflow canvas as an editor for local definitions; it is not a workflow executor or a secure team workspace.
