<h1 align="center"><img src="docs/media/readme-banner.svg" alt="Support Workflow Studio" width="100%"></h1>

<p align="center"><strong>A local workbench for drafting, checking, and approving support replies.</strong><br>
Follow a ticket from the first report to the exact words a reviewer can publish in Jira.</p>

<p align="center">
  <a href="https://github.com/Younsko/support-workflow-studio/actions/workflows/ci.yml"><img alt="Build and test status" src="https://github.com/Younsko/support-workflow-studio/actions/workflows/ci.yml/badge.svg?branch=codex%2Fclean-public"></a>
  &nbsp; · &nbsp; <a href="#demo">Watch the demo</a>
  &nbsp; · &nbsp; <a href="#quickstart">Quickstart</a>
  &nbsp; · &nbsp; <a href="docs/architecture.md">Architecture</a>
  &nbsp; · &nbsp; <a href="LICENSE">MIT license</a>
</p>

## Demo

A requester’s VPN keeps disconnecting during customer calls. Watch the ticket reach N2, pick up an approved runbook, and return to the architect when the first plan scores below 9/10.

https://github.com/user-attachments/assets/ed8991ed-b710-4aa4-974f-c2804bec74ab

**14 seconds · French on-screen text.** Turn on sound for the music and the two final clicks. The first approves the draft. The second publishes the reviewed reply to Jira. [Download the MP4](motion_demo/support-workflow-demo.mp4?raw=true)

## Quickstart

The default demo runs locally with synthetic tickets and guidance. It needs Python 3.11+ and Node.js 20+. You can run the whole review loop without a model account or a Jira tenant.

**macOS and Linux**

```sh
git clone https://github.com/Younsko/support-workflow-studio.git
cd support-workflow-studio
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn app.main:app --app-dir backend
```

<details>
<summary><strong>Windows PowerShell</strong></summary>

```powershell
git clone https://github.com/Younsko/support-workflow-studio.git
cd support-workflow-studio
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend
```

</details>

Open **http://127.0.0.1:8000**, select **SUP-4821 — VPN disconnects during customer calls**, and choose **Run workflow**. Look for `KB-104` in the evidence, then edit the proposed reply and choose **Save edit & re-review** to see how a change goes back through verification. [Guided walkthrough](docs/demo-guide.md)

To connect a Jira Service Management Cloud queue, open **Setup** in the left navigation. Enter the Jira site, account email and API token, then discover and choose a service desk and queue. The app checks queue access before saving. Select a local knowledge folder that contains the approved-source manifest, and review the automatic triage and escalation defaults. The token is stored in the local ignored `.env` file and is never sent back to the browser. [Connector setup](docs/connector-setup.md)

## Inside a run

| Stage | What you can inspect |
| --- | --- |
| Intake and routing | The original report, the suggested support tier from N1 to N3+, and the impact or recurrence clues behind it. |
| Evidence and planning | Approved guidance sits alongside open questions. Three investigation lanes examine the technical context, access and privacy, and customer impact before the architect and critic revise the plan. |
| Draft and verification | The workflow can build up to three candidate replies. A separate verifier checks the selected draft against the ticket and its sources. Specialist reviewers assess technical accuracy, security, and clarity. |
| Human decision | A draft must reach 9/10 to be eligible for approval. The reviewer can approve, reject, or edit it. Editing starts another review. |
| Jira publication | On a connected queue, **Publish public reply** is a separate action. The API rechecks the Jira ticket against the reviewed snapshot before posting. |

The timeline retains the evidence, findings, revisions, and decision for each run. The product signals view groups recurring issues and links back to the cases behind a proposed engineering escalation. [Workflow design](docs/orchestration-patterns.md)

## Configuration

- **Bundled demo** uses deterministic responses and review results. The React workbench and FastAPI share a local origin, and SQLite stores cases and decisions under `.local/`.
- **Model setup** supports demo mode, loopback Ollama, OpenAI-compatible APIs (including Groq), and Anthropic. Keys stay on the local host, external ticket inference requires explicit opt-in, and saving setup does not send a model request. [Provider setup](docs/provider-setup.md) · [Data boundaries](docs/data-boundaries.md)
- **Jira Service Management Cloud** can poll an operator-selected queue configured in the UI. Triage uses a local guidance allowlist, and sending ticket content to an external model requires explicit opt-in. [Connector setup](docs/connector-setup.md) · [Data boundaries](docs/data-boundaries.md)
- **Private local mode** pairs loopback Ollama with manually entered cases. The prototype is designed for one local operator. Shared deployments and GitHub code changes are future work. [Production readiness](docs/production-readiness.md)

## Development

For UI changes, keep FastAPI running and use `npm --prefix frontend run dev` in a second terminal. Vite proxies `/api` to the local backend. The CI workflow runs the Python tests and builds the TypeScript frontend. [Backend tests](backend/tests) · [CI workflow](.github/workflows/ci.yml)

## License and credits

The application code is distributed under the [MIT license](LICENSE). The demo features an excerpt of **« Базовый минимум » by SABI**. [Film credits](motion_demo/README.md)
