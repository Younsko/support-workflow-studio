# Provider setup

Demo mode is the default even with a Groq key. Explicitly set MODEL_PROVIDER=openai-compatible to enable live inference.

Supported modes:

- `demo`: no network calls, synthetic responses only. Set `MODEL_PROVIDER=demo` to force this mode.
- `openai-compatible` with `GROQ_API_KEY` set: the app defaults to Groq's `https://api.groq.com/openai/v1` and `openai/gpt-oss-20b`. An explicit `MODEL_PROVIDER` always takes precedence, and the Groq key is never used for a different host.
- `ollama-local`: sends prompts to an Ollama server on an HTTP loopback address only. It does not use an API key and does not require external-ticket consent.
- `openai-compatible`: a configured chat-completions endpoint, including compatible hosted or local services. Set `MODEL_BASE_URL`, `MODEL_API_KEY` if required, and `MODEL_NAME`.
- `anthropic`: Anthropic Messages API. Set `MODEL_API_KEY` and `MODEL_NAME`.

Some product subscriptions do not include API access or API credits. Check the provider's current account and billing terms. Model endpoints may receive ticket content; only use synthetic data in this demo.

The Setup UI writes non-secret scope hashes beside a saved API key and external-content consent. The runtime rejects a scoped key or consent if the selected provider or endpoint no longer matches. Existing `.env` entries without scopes fail closed until you re-enter the API key and confirm the destination in Setup. Changing providers or endpoints requires a fresh key and renewed consent; do not copy the old scope value to a new destination. The status only confirms local configuration, not network reachability or model availability.

For Jira tickets, selecting an external model without current credentials or destination-scoped consent blocks the run. The app does not silently turn that request into a demo-model run.

For a fully headless setup, calculate the scope for the exact selected provider and endpoint and set that hash as both `MODEL_API_KEY_SCOPE` and `MODEL_CONSENT_SCOPE` after reviewing the destination:

```sh
python -c "import sys; sys.path.insert(0, 'backend'); from app.providers import model_destination_scope; print(model_destination_scope('openai-compatible', 'https://api.groq.com/openai/v1'))"
```

Example `.env` for a compatible endpoint:

```dotenv
MODEL_PROVIDER=openai-compatible
MODEL_BASE_URL=https://your-approved-endpoint.example/v1
MODEL_API_KEY=replace-me
MODEL_NAME=your-model-id
```

For Ollama Cloud, Ollama documents the OpenAI-compatible base URL as `https://ollama.com/v1`; configure an Ollama API key and an available cloud model name. Set `OLLAMA_API_KEY` or use the Setup field for the model API key; the key still needs a matching `MODEL_API_KEY_SCOPE`. Cloud ticket inference also needs a separate, destination-matched consent. Ollama Cloud currently does not support structured outputs, so this connector omits `response_format` for that endpoint, asks for JSON in the system instruction, and validates the returned content in the workflow. Do not treat that instruction as a schema guarantee: malformed or incomplete model output must fall back or fail its deterministic checks. This connector uses chat completions and does not assume other OpenAI API features are supported. See [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) and [Ollama Cloud](https://docs.ollama.com/cloud).

For local Ollama, set the native endpoint without a token:

```dotenv
MODEL_PROVIDER=ollama-local
MODEL_BASE_URL=http://127.0.0.1:11434
MODEL_API_KEY=
MODEL_NAME=your-installed-model-tag
```

The provider rejects non-loopback hosts, disables inherited proxy settings, checks Ollama's `/api/tags`, rejects models with less than 16 MiB of reported local model data, then calls `/api/chat`. This is a safeguard against Ollama Cloud tags; a modified local daemon or another process could still relay data. Set `PRIVATE_LOCAL_MODE=true` to require `ollama-local` and block Jira settings, external inference, model council profiles, queue sync, and reply writes. Enter only reviewed, minimized local summaries in this mode.

The server enforces request timeouts and per-run call caps (`MAX_MODEL_CALLS_PER_RUN` defaults to 24). `MODEL_MAX_OUTPUT_TOKENS` bounds each response (default `2048`, accepted range `128`–`2048`). Groq GPT-OSS uses its `max_completion_tokens` field, low reasoning effort, hidden reasoning output, and strict JSON Schema output for the plan critic; other structured stages use JSON-object mode. OpenAI-compatible and Anthropic calls retry HTTP 429 responses at most twice, using the provider's token-reset header when available. A Groq schema-validation failure gets at most two bounded retries with an explicit all-fields reminder. OpenAI-compatible endpoints must use HTTPS, except HTTP endpoints on loopback for local model servers. Provider errors are surfaced in the workflow trace. For a real deployment, add a managed secret store, tenant-level egress controls, provider allowlists, data processing review, rate limits, and usage budgets before accepting real tickets.

For a live model council, configure `COUNCIL_PROVIDER`, `COUNCIL_MODEL`, and `COUNCIL_API_KEY` for a distinct second proposal profile; `COUNCIL_BASE_URL` is used for an OpenAI-compatible endpoint. Also configure `JUDGE_PROVIDER`, `JUDGE_MODEL`, and `JUDGE_API_KEY` for a third, distinct model; `JUDGE_BASE_URL` applies to an OpenAI-compatible judge. Each profile must also have its own exact destination scope in `COUNCIL_API_KEY_SCOPE` or `JUDGE_API_KEY_SCOPE`. For an external profile, separately set `ALLOW_COUNCIL_EXTERNAL_TICKET_INFERENCE=true` or `ALLOW_JUDGE_EXTERNAL_TICKET_INFERENCE=true` after review and set the matching `*_CONSENT_SCOPE`. A primary-model consent never grants consent for council or judge destinations. The runtime skips the council unless all required profile scopes and consents match. Use different model families where possible. The proposal profiles produce strategies, then the judge receives the request, approved evidence, and anonymous Option A/Option B texts in a fresh call. Configured provider and model identifiers are redacted from proposal text when they appear verbatim. The judge returns scores and rationales for evidence use, uncertainty, safe diagnostics, and next steps. Provider identities are not provided as metadata or shown beside proposals. Council output is advisory and never approves a response. The optional council uses three calls from the per-run model-call budget.

## Jira Service Management Cloud

The connector reads one human-selected service-desk queue and can publish one **public** comment only after the workflow draft has passed the 9/10 gate and a reviewer has approved it in the UI. The API process monitors the queue every `JSM_POLL_INTERVAL_SECONDS` (15–900 seconds, default 60) while that process is running; it follows Jira's `start`/`limit` pagination and scans up to 200 queue items per cycle. If the cap is reached, the UI shows a warning; split high-volume work into narrower queues. Pending auto-triage IDs outside a truncated scan are checkpointed in local SQLite and are not processed until Jira shows them in the configured queue again. The workbench refreshes its local view every 15 seconds. Set `JSM_AUTO_TRIAGE_NEW_TICKETS=true` to build review-only workflows for tickets first detected while auto-triage is enabled and created after both the first successful poll and the latest opt-in activation time; older backlog items are not picked up when enabling auto-triage. Disabling auto-triage pauses new work and preserves already pending retries for a later re-enable. The monitor never sends a reply or creates a code commit. `JSM_AUTO_TRIAGE_MAX_PER_POLL` caps automatic runs to 1–10 newest arrivals per poll (default 5); remaining arrivals stay pending for a later poll and remain available for manual triage. Caught triage failures remain pending and are retried on later polls up to three attempts per ticket; missing knowledge or external-inference consent pauses the review-only queue. Before a pending workflow starts, the connector re-reads the request and skips tickets Jira marks complete. On restart, seen-ticket, baseline and retry checkpoints are restored from SQLite. There are no distributed leases, so run one API process only. Start with a test project and synthetic requests.

Open **Setup** in the app to enter the Jira site URL, account email, API token, service desk ID and queue ID. The app checks that it can read a sample from the selected queue before saving. The token is written by the backend to the local ignored `.env` file and is never returned to the browser. Environment variables remain available for headless local configuration. The connector validates a canonical HTTPS `atlassian.net` site URL, follows no redirects, limits reads to 50 queue items, and retries read-only HTTP 429 responses at most twice only when a valid `Retry-After` requests a wait of five seconds or less. Longer or missing retry instructions are returned to the caller without an early retry. It never retries a comment write automatically. Before auto-triage or publishing, it re-reads request details from the [Jira Service Management request API](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-request/); it skips auto-triage when `currentStatus.statusCategory` is `DONE` and rejects publishing if Jira's current summary or description differs from the immutable content fingerprint stored on the approved workflow run. A queue sync cannot replace that reviewed snapshot. The final read and public-comment write are separate requests, so a ticket edit in that short interval cannot be excluded by an atomic condition in this prototype. Timeouts, server errors, or malformed success replies are treated as unknown outcomes; inspect Jira before retrying.

The API only accepts loopback connections and loopback hostnames; browser-originated mutation requests are accepted only from the same origin or the two configured Vite development origins. This prototype still has no user authentication or roles, so any process running as the local user remains within the trust boundary. Approval decisions, run records, and monitor checkpoints are stored in local SQLite. After a restart, inspect Jira manually before retrying an interrupted comment; a comment write may have succeeded even if the local outcome record was not saved. Pending triage work resumes from its SQLite checkpoint when Jira returns the ticket in the configured queue.

The connector can read live tickets while AI inference remains in demo mode. Live model calls with JSM ticket text are blocked unless the operator explicitly sets `ALLOW_EXTERNAL_TICKET_INFERENCE=true` after reviewing the configured provider's terms and data handling. This is a local prototype switch, not an enterprise consent or privacy control.

Before running triage on Jira requests, choose a local folder in **Setup**. It must contain the operator-approved support guidance and an explicit `.support-workflow-approved-sources.json` manifest. `KNOWLEDGE_DIR` remains available for headless setup. Example:

```json
{
  "version": 1,
  "approved_sources": [
    {
      "path": "vpn/connection-checks.md",
      "approval_status": "approved",
      "owner_ref": "<operator-managed owner reference>",
      "approved_at": "<approval date>",
      "approval_evidence_ref": "<operator-managed evidence reference>"
    }
  ]
}
```

Each manifest entry must state `approval_status: approved` and include non-empty owner, date and approval-evidence references. Adding a record is the operator's explicit selection for retrieval; the app does not verify an organization's approval policy or the referenced evidence. The loader reads only listed `.md` and `.txt` files, rejects absolute paths and traversal, skips symlinks outside the folder, caps the index at 200 files and 5 MB total, and limits each retrieved source excerpt to 12,000 characters. It shows the number of loaded sources in integration status but does not send those references or source paths to the model. Jira triage is blocked when the manifest is missing, invalid, incomplete or has no readable listed files; unlisted documents are ignored. If no article matches a request, the workflow has no evidence and its score gate prevents publication. The folder path is not sent to the model, but retrieved article text can be sent along with ticket text when external model inference is explicitly enabled.

For this single-operator local prototype, an administrator supplies their own API token locally. Do not collect or share tokens in chat, screenshots, browser storage, or a public deployment. Atlassian recommends a single distributable OAuth 2.0 (3LO) app for distributed integrations and warns against apps collecting individual user API tokens. Before a company pilot, replace local token configuration with the approved OAuth installation and least-privilege scopes. Queue reads require Jira work/queue access; public comments require the user's Add Comments permission. See the [Jira Service Management queue API](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/), [request comment API](https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-request/), and [Atlassian authentication guidance](https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/).

## GitHub code commits

The planned code-change path targets GitHub and will create commits on a new dedicated branch after a separate human confirmation. The target repository and validation commands have not been supplied yet, so this prototype does not request GitHub credentials or create code commits. It also has no code patch generation, isolated runner, or commit endpoint. Those details must be configured before wiring the integration.
