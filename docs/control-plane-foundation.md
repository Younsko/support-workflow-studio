# Control-plane identity foundation

This package introduces a metadata-only ASGI service at `app.control_plane.api:app`, intended to run separately from the ticket-processing application. It validates OIDC bearer access tokens and stores verified identities, team workspaces, membership roles, invitations, and metadata audit events in a dedicated Postgres database configured by `SUPPORT_CONTROL_PLANE_DATABASE_URL`.

## Identity-provider contract

Configure `CONTROL_PLANE_OIDC_ISSUER`, `CONTROL_PLANE_OIDC_AUDIENCE`, and `CONTROL_PLANE_OIDC_JWKS_URI`. The issuer and JWKS endpoint must be HTTPS. The API accepts only RS256-signed bearer JWTs with the exact configured issuer and audience, required `sub`, `iat`, and `exp` claims, and a verified email claim. User subjects are taken from the validated token. Workspace IDs and roles from request payloads never establish access.

Install the `control-plane` package extra to add PyJWT and its cryptographic backend. The API has no password table or local password authentication. Email sign-in and multi-factor policy are delegated to the configured OIDC identity provider.

## Workspace API

- `GET /api/v1/me` returns the verified identity and its memberships.
- `GET/POST /api/v1/workspaces` lists accessible workspaces or creates one with the caller as Owner.
- `GET /api/v1/workspaces/{id}/members` requires workspace membership.
- `PATCH/DELETE /api/v1/workspaces/{id}/members/{subject}` applies Owner/Admin policy, preserves at least one Owner, restricts Admin-role grants and Admin removal to Owners, and records the authenticated actor.
- `POST/GET /api/v1/workspaces/{id}/invitations` creates bounded, one-time invitations or lists pending/history records for Owners/Admins. Admins can invite lower roles; only Owners can grant Admin or Owner. The raw random invitation token is returned once with `Cache-Control: no-store`; only its SHA-256 digest is stored.
- `POST /api/v1/invitations/{id}/redeem` requires a valid bearer identity whose verified email exactly matches the invitation.
- `GET /api/v1/workspaces/{id}/audit` exposes bounded metadata events to Owners/Admins.

The invitation token is not emailed yet; the authenticated workspace administrator must deliver the one-time link through an approved channel. Production onboarding still needs a verified email sender, rate limiting, abuse controls, recovery flows, and operational key-rotation policy.

## Data separation and current limits

The control-plane database stores no ticket bodies, drafts, evidence, provider credentials, runner secrets, or workflow execution payloads. Keep this database separate from the customer runner's `SUPPORT_WORKFLOW_DATABASE_URL`; when both URLs are configured in one process, startup compares their configured Postgres host, port, and database name and fails closed when they point to the same target. This check cannot detect distinct DNS aliases for the same database service, so deployments must also use separate database identities and verify separation operationally. The runner APIs and visual UI are not yet integrated with these identities and workspaces; the old local application remains loopback-only and must not be treated as protected by this service. Workspace isolation is proven only for these metadata endpoints. Runner enrollment, secret delivery, browser/runner encryption, billing, chat connectors, and large-scale readiness remain future work.

Use `Dockerfile.control-plane` for the isolated service image. Supply an OIDC audience intended for this API and a dedicated Postgres URL through the deployment secret manager. Do not expose the current runner app to the network until every support-data route is migrated behind authenticated, workspace-scoped authorization.
