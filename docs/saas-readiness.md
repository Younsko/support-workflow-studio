# SaaS readiness and delivery gates

This document distinguishes the current local prototype from the hosted product direction. It is not a claim that hosted SaaS, tenant isolation, or encrypted browser-to-runner transport is already implemented.

See [the security threat model](threat-model.md) for the current trust boundaries, verified prototype controls, and production security gates.

## Current product boundary

The repository currently provides a single-operator FastAPI and React prototype. It has a code-defined ticket runtime plus a separate visual workflow editor, a local versioned-definition API, synthetic demo cases, a Jira Service Management Cloud adapter, local knowledge-folder retrieval, and provider adapters for demo, OpenAI-compatible APIs, Anthropic, and local Ollama. A draft must pass deterministic review gates and receive a human decision; publishing a Jira reply is a separate action. JSM has not been validated against a real customer sandbox as part of this release.

The current app is not a multi-user SaaS. The visual editor can save, validate, publish, and fork local workflow definitions. A fail-closed compiler connects the exact linear starter sequence to the existing ticket runtime when Jira, knowledge, and model bindings match; unsupported graphs are rejected. Runs require a Jira-linked case, record the immutable workflow version/hash and node IDs, use the configured agent role and bounded drafting guidance, and preserve separate human approval and publication. Definitions are stored in local SQLite. The Setup panel configures one local Jira Service Management connection, either an approved local knowledge folder or a read-only, scope-limited Confluence Cloud source, triage rules, and demo/API/Ollama model settings. There is no sign-in, workspace membership or role enforcement, hosted control plane, durable runner protocol, ServiceNow connector, end-to-end encrypted review channel, hosted billing, or production SLO. Ticket and monitor checkpoints and published-run stage journals persist to SQLite on one host; published-run leases are renewed after completed stages and fenced by owner, but an interrupted run restarts from its beginning. There is no distributed worker coordination or multi-worker guarantee. Do not use this release to claim production readiness or large-scale capacity.

### Implemented in this branch

- Typed version-1 workflow graph definitions, deterministic graph/configuration validation, per-node configuration allowlists, read-only agent-tool allowlists, secret-key rejection, and a required approval gate on every path to a publish node.
- A shared English/French locale switch for the application shell, inbox, Setup panel, and visual workflow editor. The locale preference is stored in the browser; ticket text and model-generated content remain in their source language unless a workflow explicitly translates them.
- A five-step local setup guide for ticket source, approved knowledge, model, triage rules, and a final review. It shows local/demo defaults, automatic-intake behavior, saved-but-untested model state, and a secret-free summary before saving.
- A bilingual visual workflow editor with a starter support flow, node palette and inspector, local validation, save, immutable version publish/fork controls, and a run action for the currently supported starter graph.
- A published-version history in the canvas showing immutable version numbers, publication timestamps, and content hashes, with an explicit latest-version fork action and stale-draft handling.
- Keyboard-accessible move-earlier/move-later controls for the current linear sequence; the ticket trigger stays first, and any order/topology outside the supported starter graph fails closed at run time.
- A local, read-only Confluence Cloud knowledge source with an operator-selected space allowlist, explicit site/space-bound consent for minimized ticket search terms, user-triggered connection check, bounded reads, current-page revalidation, and source-version metadata in evidence. No real tenant has been validated in this release.
- Canvas Connections controls open the local Setup panel and reflect Jira, approved knowledge, and model-configuration state in the draft's declarative references. Primary model keys and external-data consent are bound to the chosen provider and endpoint; optional council and judge profiles require separate scoped keys and consent. The UI reports remote model settings as saved but untested.
- Loopback-only `/api/v1/workflows` endpoints backed by local SQLite, revision checks against stale drafts, HTTP 409 conflicts when a fork already exists, immutable published snapshots, integrity-checked content hashes, metadata-only local audit events, and `POST /api/v1/workflows/{id}/runs` for the supported starter graph.
- A workflow execution compiler that checks the exact linear node/edge sequence, configured Jira and knowledge references, active model binding, read-only knowledge-search tool allowlist, approval role gate, and current non-secret connector/model destination fingerprint before any external ticket read. Runs retain their published workflow ID, version, hash, and node IDs.
- Published workflow starts use a persisted idempotency key and an expiring, owner-fenced SQLite lease. A completed retry returns the stored run; an active duplicate is rejected; an expired or failed claim can be reacquired without reusing an external write. Claim completion and run persistence commit together. This is still a one-host local safeguard, not the planned Postgres multi-worker runner.
- Published-run idempotency binds the key to case, published workflow version/hash, active connector/model bindings, candidate count, and model-council option. Completed stages append a content-free checkpoint and renew the owner-fenced lease; `GET /api/workflows/{run_id}/execution-steps` exposes only run ID, attempt, ordinal, stage ID, status, duration, and timestamp. The review timeline shows the persisted attempt and duration for each stage of a published run. These records support progress inspection, not step-level resume: a failed or expired attempt reruns from the start.
- Jira monitor seen-ticket, baseline, and retry checkpoints persisted to local SQLite so one local process can resume pending triage after restart; attempts are reserved before provider work and stop after three interrupted or failed starts.
- Freshness checks compare the live Jira summary, description, and status with the source snapshot before a human approves or requests model review of an edited draft, and again immediately before a public reply. Stale work is blocked and must be synced and triaged again.
- Run lifecycle metadata events persisted in local SQLite for workflow start, human edit/approval/rejection, and public-reply publish attempts/outcomes. The review screen shows this activity alongside the workflow stages. The read-only `/api/workflows/{run_id}/events` endpoint exposes only stable IDs, timestamps, state transitions, approval version, score, and publish outcome; the allowlisted schema excludes ticket bodies, drafts, secrets, and arbitrary strings.
- A connector development guide documents the shipped ITSM adapter contract, failure semantics, synthetic conformance expectations, and current connector gaps.
- Operational `/health/live` and `/health/ready` probes separate process liveness from local SQLite readiness. Readiness reports no model/provider choice or credential presence and returns a generic 503 if storage is unavailable; this is a local single-process check, not a hosted dependency health system.
- Synthetic tests covering workflow validation, persistence after registry reopen, stale revisions, publish/fork behavior, provider-key and consent scoping, and monitor restart/retry recovery.

These are local prototype capabilities. Local audit events are not tamper-evident and do not identify authenticated actors. Run lifecycle events cover workflow starts, human decisions, and publish attempts/outcomes; they do not yet provide authenticated identity, tenant scope, a trusted clock, or a complete tamper-evident audit trail. The Setup panel stores Jira and model API keys in the local ignored `.env` file. The canvas uses the current local connection status to fill stable reference IDs, but these references are not tenant-scoped and credentials are not delivered to a customer runner. There is no user authentication, tenant isolation, hosted SaaS persistence, or secure remote collaboration.
The starter canvas can be saved and published locally. The exact supported starter version can run on a Jira-linked case only while its Jira, knowledge, and model bindings match; all other graph forms are rejected before provider calls. The workflow Connections control opens the existing local Setup panel; it does not create a hosted connector registry. Conditional branching remains non-executable until the editor can represent explicit branch ports and runtime handlers execute them safely.

## Team entry points

Teams and Slack are a product expansion after the private JSM + Confluence pilot. They should make support workflows reachable where teams already work, while Jira/ServiceNow remains the system of record for ticket status, assignments, and published replies.

The first channel release should support an explicitly installed workspace bot, scoped event subscriptions, ticket intake from a message or command, status notifications, and authenticated deep links to the review UI. A chat action may create or route a ticket and may request a draft; it must not bypass workflow policy, human approval, or the separate publish action. Approval should remain in the authenticated application until channel identity mapping, message-context freshness, and audit behavior are proven.

Ship one channel connector first (Teams or Slack, selected from design-partner demand), then add the other through the same versioned channel-adapter contract. Require least-privilege bot scopes, workspace-admin installation, signature verification, replay protection, idempotent event handling, explicit retention and data-flow disclosure, and tenant-isolation tests. Do not mirror full ticket bodies into chat by default; send a short redacted summary and a link, with any richer disclosure configured by workspace policy.

## Target deployment boundary

```mermaid
flowchart LR
  Browser[Workspace browser]
  CP[EU hosted control plane\naccounts, workflow metadata, run metadata]
  Relay[Opaque encrypted relay]
  Runner[Customer Docker runner\nconnectors, models, durable runs]
  ITSM[Jira Service Management\nthen ServiceNow]
  KB[Confluence\nthen other approved sources]
  Model[Workspace-selected model\nBYOK API or local Ollama]

  Browser -->|workspace and workflow metadata| CP
  Browser <-->|end-to-end encrypted ticket and draft payloads| Relay
  Runner <-->|outbound authenticated session| Relay
  Runner --> ITSM
  Runner --> KB
  Runner --> Model
```

Ticket and draft bodies must remain readable only to authorized browser clients and the customer runner in private mode. The hosted relay may route ciphertext and minimal routing metadata, but must not log or persist plaintext content. The customer-selected ITSM, knowledge service, and model provider receive only the content required for their configured workflow; setup must disclose that processing before activation. A cloud-readable processing mode, if added, must be a separate explicit workspace opt-in.

End-to-end encryption is a release-blocking security design task. Use a reviewed protocol/library and document workspace-key creation, browser/runner enrollment, recovery, member removal, device revocation, and rotation before enabling the relay. Do not implement a home-grown cryptographic protocol or describe the relay as encrypted until independent tests verify that the control plane cannot decrypt payloads.

## Release sequence

1. **Workflow foundation:** typed definitions, validation, local immutable versions, draft revision checks, a constrained starter-graph compiler, persisted local idempotency/lease claims, content-free per-stage execution journals, and stage-triggered owner-fenced lease renewal are implemented in this branch. Next add tenant-scoped Postgres persistence, schema migrations, resumable step-level execution, a transactional event outbox, distributed lease renewal/fencing, and a customer runner. Keep existing `/api/workflows` ticket-run endpoints compatible until the durable runtime is proven.
2. **Visual editor:** a bilingual local editor with a starter flow, palette, inspector, save, validate, publish, fork, and supported-flow run actions is implemented in this branch. Next add execution timelines keyed to typed node events, then expand graph execution only alongside per-node handlers and policy tests.
3. **Team control plane:** add email sign-in, invitation flows, workspace isolation, and Owner/Admin/Builder/Operator/Approver/Viewer permissions. All workspace-owned rows and events must be tenant-scoped and authorization-checked server-side. Add rate limits and an audit trail before inviting external teams.
4. **Customer runner:** package guided Docker deployment, outbound enrollment and heartbeat, revocable credentials, secret delivery to the runner, durable run/step state, retry leases, idempotency keys, and backup/restore instructions. Runner outages must pause safely and resume without duplicate external writes.
5. **Private pilot:** implement and test the encrypted browser-runner review channel. Add Confluence after permission, freshness, and source-identity behavior are tested. Validate JSM read and separately approved publish against a dedicated test tenant using synthetic tickets only.
6. **Team beta and production:** add ServiceNow, hosted health and usage reporting, retention controls, support/incident runbooks, SLO and recovery targets, and measured concurrency/load limits. After the JSM/Confluence pilot, add one Teams or Slack entry-point integration based on partner demand; add the second only after shared channel security and event contracts are proven. Add billing only after pilot demand is validated; defer SSO, Kubernetes, and multi-region until customer requirements justify them.
7. **Career evidence:** publish an architecture and threat model, data-flow diagram, reproducible Docker guide, synthetic end-to-end demo, CI evidence, and benchmark results measured on named hardware/configuration. Keep claims tied to evidence.

## Acceptance gates

- Workspace data is isolated across users; every role has allow/deny tests for workflow changes, connector setup, approvals, and publishing.
- Secrets never appear in browser telemetry, logs, workflow definitions, or the control-plane database. Connector credentials are delivered to and stored by the runner.
- Published workflow versions are immutable; new edits create drafts. Invalid node configurations, dangling edges, cycles, and unsupported nodes are rejected before activation.
- A runner restart or disconnect preserves run state. Duplicate events and retries do not duplicate external writes; uncertain write outcomes require inspection rather than automatic retry.
- Approval refers to the precise draft/ticket snapshot shown to the approver. Changed source content invalidates that approval.
- The relay/control plane cannot decrypt private review content. Revoked members/devices cannot fetch new workflow keys or payloads.
- Teams/Slack events are authenticated, replay-safe, idempotent, workspace-scoped, and covered by retention and redaction rules; channel commands cannot bypass approval or publish policy.
- English and French cover onboarding, editor, validation, connector errors, review, and recovery flows.
- CI builds the backend and frontend and runs synthetic security, connector-contract, workflow, and access-control tests. The protected release branch requires those checks and blocks force-push and deletion.
- Load tests report throughput, latency, queue delay, and recovery behavior. No scale claim is made beyond measured results.

## Scope guardrails

- Public fixtures and demo recordings use synthetic data only. Never commit ticket exports, private identifiers, customer runbooks, credentials, or live model outputs containing real support text.
- V1 agents are bounded workflow nodes with explicit prompts, model bindings, and allowlisted tools. No arbitrary code execution or unrestricted tool servers.
- The starter path is one customer runner host and a small team workspace; horizontal workers and larger deployments follow measured need.
- A green build proves software checks passed; it does not by itself prove tenant isolation, cryptographic privacy, or production readiness.
