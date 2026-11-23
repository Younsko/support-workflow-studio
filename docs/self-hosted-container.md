# Self-hosted container preview

This Compose package runs the current single-operator prototype on one customer-controlled machine. It binds the web app to `127.0.0.1`, persists local workflow state in a named Docker volume, runs as a non-root user, drops Linux capabilities, and uses a read-only container filesystem. It does not add user accounts, workspace isolation, a remote runner protocol, or production multi-worker support.

## Prerequisites

- Docker Engine with the Compose plugin, or Docker Desktop with Compose enabled.
- A machine where local operators can access `http://127.0.0.1:8000`.

## Start

From the repository directory, start the service:

```powershell
docker compose up --build --detach
```

Open `http://127.0.0.1:8000`. The default provider is deterministic demo mode and the UI serves from the same origin as the API. Configure Jira, approved knowledge, and a model in Setup only after reviewing the data flow and the selected services' access. Do not put credentials in the image, Compose file, or source control.

The Setup screen writes operator settings to `settings.env` inside the named `workflow-state` volume; SQLite workflow state uses that same volume. The container root filesystem stays read-only. Keep the Docker volume within the intended local trust boundary, back it up before upgrades, and use the documented offline SQLite backup/restore procedure.

## Operate

```powershell
docker compose ps
docker compose logs --tail 100 support-workflow-studio
docker compose stop
docker compose up --detach
```

To build a fresh image after pulling code, run `docker compose build --pull` and then `docker compose up --detach`. Do not remove the named volume during routine upgrades. Removing the Compose project with `docker compose down --volumes` permanently deletes its local workflow state.

## Current limits

- The package uses SQLite by default. The optional Postgres runtime store is configured separately and is not wired into this Compose preview.
- The Setup UI persists its credentials in the local Docker volume. They are not included in the image or committed configuration; protect Docker volume access as local secret access.
- The workflow-definition registry still uses SQLite, and the app's caches and monitor locks are in process.
- The app remains unauthenticated and reachable only from the same machine. Do not publish port 8000 on a network interface or expose it through a reverse proxy.
- This image is an installation preview, not the planned customer runner paired with a hosted control plane. There is no enrollment, heartbeat, secure secret delivery, end-to-end encrypted review, or remote revocation yet.
