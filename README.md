# Support Workflow Studio

A support automation workflow for receiving requests, retrieving approved guidance, proposing a response, checking it independently, and waiting for a human decision. Each run exposes its evidence, plan, review scores, revisions, and outcome.

**This is a local prototype.** It ships with synthetic tickets and knowledge and has an optional operator-configured Jira Service Management Cloud queue connector. Jira connection is off by default. A workflow score of 9/10 is a quality gate, never a substitute for human approval. Publishing a public reply requires approval of the draft and a second explicit publish action. GitHub code commits are not implemented yet.

The repository contains synthetic requests and guidance only. Keep real support data and confidential documentation out of public files, issues, screenshots, and model prompts. See [docs/data-boundaries.md](docs/data-boundaries.md).

The optional `PRIVATE_LOCAL_MODE=true` guard restricts a local copy to a local Ollama model, refuses Jira settings and external model profiles, and blocks Jira sync and public reply writes. It accepts manually entered summaries only. Keep confidential data outside this repository and verify local inference before use.

## What you can explore

- A case queue with synthetic support requests and clear separation between reported text, inferred fields, and verified evidence.
- A provisional N1/N2/N3/N3+ routing suggestion with deterministic escalation clues for reported broad impact, security, and recurrence; support tiers and urgency are visible in the review UI and remain subject to human confirmation.
- A traceable workflow run: intake → independent investigation lanes → plan and critique loop → isolated response candidates → independent verifier → specialist review lanes → revision gate → human decision, with an optional three-model blind council.
- Deterministic checks for evidence coverage, unresolved facts, safety and response completeness, alongside model-generated judgments.
- A deterministic demo mode that runs without credentials, plus model adapters for local Ollama, OpenAI-compatible APIs, and Anthropic; the operator-selected local `.md`/`.txt` knowledge folder has an explicit file allowlist for Jira triage.
- An optional Jira Service Management Cloud queue monitor in the API process, with configurable review-only auto-triage, bounded workflow-failure retries, and a separately confirmed public-comment action.
- An audit timeline with stage outcomes, iteration counts, estimated latency, and explicit review state.
- Session metrics derived from actual cases and workflow runs: open requests, live-model runs, median triage generation time, quality-gate outcomes, reviewer approvals/edits/rejections, and published replies.
- A read-only product-signal panel that groups multiple completed triages by their inferred category, links back to the source requests, and can copy a human-review escalation brief without creating an engineering ticket.

## Run locally

Requirements: Python 3.11+ and Node.js 20+.

```powershell
python -m venv .venv
\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
cd frontend
npm install
npm run build
cd ..
uvicorn app.main:app --app-dir backend
```

Open `http://127.0.0.1:8000`. FastAPI serves the built frontend from `backend/app/static` when its `index.html` is present, keeping the UI and API on one local origin. The Vite build goes into that Python package directory so a built wheel can include the frontend assets. During UI development, keep the backend running in another terminal and run Vite separately instead of building; Vite proxies `/api` to `http://127.0.0.1:8000`:

```powershell
cd frontend
npm run dev
```

Open the local URL printed by Vite. Without model credentials, the workflow uses clearly labeled synthetic demo outputs. See [docs/provider-setup.md](docs/provider-setup.md) to configure an OpenAI-compatible endpoint or Anthropic Messages API. Provider subscriptions and API access are separate products in some services; verify your account's access and billing before enabling live inference.

## Project boundaries

- Start with synthetic Jira requests. Use a real queue only from an authorized local operator session and after your organization approves the connector and data handling; this prototype has no user accounts, roles, or durable audit log.
- Never paste customer data, credentials, or recovery codes into chat, screenshots, or issues. If live model inference is explicitly enabled, Jira ticket text and matched local guidance are sent to that configured model provider.
- Ticket content and retrieved knowledge are untrusted input. They cannot authorize a tool or override workflow policy.
- Generated responses remain drafts until the reviewer approves them. Publishing a Jira reply is a second explicit action, and the API only accepts connections from the local machine.
- With no JSM settings, no external system is read from or written to. Enabling JSM reads the selected queue while the API runs; new-ticket auto-triage is opt-in, never publishes replies or commits, and requires approved local knowledge. Public comment writes require the two human actions described above.
- Jira triage stays disabled until `KNOWLEDGE_DIR` contains `.support-workflow-approved-sources.json` listing the guidance files allowed for retrieval. When a live model is used, both ticket text and retrieved guidance are sent to that configured provider only after `ALLOW_EXTERNAL_TICKET_INFERENCE=true` is set.
- Provider calls are opt-in, bounded, and can incur cost. Do not expose API keys in screenshots or logs.
- Tickets, workflow runs, approvals, and comment outcomes are stored locally in `.local/state.sqlite3` (or `.local/private-state.sqlite3` in private mode). Set `SUPPORT_WORKFLOW_DB_PATH` to change that path. The database is ignored by Git and is not a shared or authenticated production store. After an interrupted Jira write, inspect Jira manually before taking any further action.

## Architecture

See [docs/architecture.md](docs/architecture.md), [docs/orchestration-patterns.md](docs/orchestration-patterns.md), [docs/demo-guide.md](docs/demo-guide.md), and [docs/production-readiness.md](docs/production-readiness.md). The UI uses dark green `#0A3323`, moss `#839958`, beige `#F7F4D5`, rosy brown `#D3968C`, and midnight green `#105666`.

## Research note

The orchestration showcase follows [YK's Google Cloud Community synthesis](https://medium.com/google-cloud/5-agent-orchestration-patterns-backed-by-research-54be9f79171d) and its [original X post](https://x.com/googlecloud/status/2123428849262248255). The article draws on several separate studies; its benchmark gains and regressions are task-specific, not general production guarantees. The patterns in this app are practical workflow designs, not a reproduction of one study's benchmark architectures.

- [Google Research: Towards a science of scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [Nature Machine Intelligence: Capable language models can outgrow the benefits of collaboration](https://www.nature.com/articles/s42256-026-01268-y)

## License

MIT. See [LICENSE](LICENSE).
