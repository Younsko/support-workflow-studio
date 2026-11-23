# Production readiness

This repository is a local prototype with synthetic examples. It can poll an operator-configured Jira Service Management queue, perform review-only triage, and publish a reply only after separate human approval and a publish action. A loopback-only Docker Compose package makes the single-operator prototype easier to run, but it does not include user accounts, tenant isolation, managed secrets, an enrolled customer runner, or a production audit service.

The current product direction is a hosted team control plane paired with an open-source runner on a customer-controlled host, for support teams of about 5–50 people. Start EU-first, use Jira Service Management plus Confluence for the synthetic-data pilot, and let customers bring model API credentials or use local Ollama. The vendor identity, legal basis, retention periods, and concrete SLOs remain undecided.

Before using real requests in a hosted or shared deployment, define and verify:

- Ticket source, approved read and write actions, and the exact customer-send policy.
- Identity provider, reviewer roles, tenant isolation, and audit access.
- Hosting region, network egress, encryption, backups, and recovery.
- Data classification, retention, deletion, residency, and model-provider terms.
- Volumes, concurrency, latency targets, availability objectives, and usage budgets.
- Escalation ownership, business-hours policy, and severity matrix.
- Knowledge ownership, review cadence, source freshness, and conflict resolution.
- Any code-change target, allowed paths, test environment, branch protection, and CI requirements.

The local SQLite file preserves prototype state across restarts but is not a shared authenticated store. A real deployment requires tenant-scoped persistence, access controls, threat modeling, privacy review, approved model providers, and evaluation against representative cases. Automated code commits are outside the current prototype; add them only with a separate approval and verified tests.

## Local SQLite backup and restore

The single-host prototype includes a standard-library backup command. It uses SQLite's online backup API to capture a consistent snapshot, checks the copy with `PRAGMA integrity_check`, publishes it atomically without overwriting an existing backup, and applies owner-only file permissions on non-Windows hosts. Keep backups in a protected location and apply the host's normal access controls on Windows.

```powershell
$env:PYTHONPATH = "backend"
python -m app.backup create C:\secure-backups\support-workflow-2026-11-23.sqlite3
```

Restore only while the application is stopped. A new target is created by default; replacing a database requires `--replace`, first creates a timestamped pre-restore safety copy beside the target, and then atomically swaps in the verified snapshot.

```powershell
$env:PYTHONPATH = "backend"
python -m app.backup restore C:\secure-backups\support-workflow-2026-11-23.sqlite3
python -m app.backup restore C:\secure-backups\support-workflow-2026-11-23.sqlite3 --replace
```

These commands cover the current local SQLite deployment only. They are not a Postgres backup strategy or evidence of a tested hosted recovery target. Add scheduled encrypted backups, restore drills, retention, and recovery objectives before a customer pilot.
