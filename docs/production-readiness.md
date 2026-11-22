# Production readiness

This repository is a local prototype with synthetic examples. It can poll an operator-configured Jira Service Management queue, perform review-only triage, and publish a reply only after separate human approval and a publish action. It does not include user accounts, tenant isolation, managed secrets, or a production audit service.

Before using real requests, define and verify:

- Ticket source, approved read and write actions, and the exact customer-send policy.
- Identity provider, reviewer roles, tenant isolation, and audit access.
- Hosting region, network egress, encryption, backups, and recovery.
- Data classification, retention, deletion, residency, and model-provider terms.
- Volumes, concurrency, latency targets, availability objectives, and usage budgets.
- Escalation ownership, business-hours policy, and severity matrix.
- Knowledge ownership, review cadence, source freshness, and conflict resolution.
- Any code-change target, allowed paths, test environment, branch protection, and CI requirements.

The local SQLite file preserves prototype state across restarts but is not a shared authenticated store. A real deployment requires tenant-scoped persistence, access controls, threat modeling, privacy review, approved model providers, and evaluation against representative cases. Automated code commits are outside the current prototype; add them only with a separate approval and verified tests.
