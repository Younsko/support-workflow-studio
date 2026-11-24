"""Postgres metadata store for accounts, team workspaces, and invitations.

This store intentionally contains no tickets, drafts, evidence, connector keys,
model keys, or runner state. It must use a separate control-plane database URL.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from .auth import Principal

WorkspaceRole = Literal["Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"]
WORKSPACE_ROLES = frozenset(
    {"Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"}
)
_SAFE_WORKSPACE_ID = re.compile(r"^[a-f0-9]{32}$")
CURRENT_SCHEMA_VERSION = 3
_SCHEMA_MIGRATION_LOCK_KEY = 0x53575343504D4554


class WorkspaceNotFound(LookupError):
    pass


class MembershipDenied(PermissionError):
    pass


class WorkspaceConflict(ValueError):
    pass


class InvitationInvalid(ValueError):
    pass


class RunnerCredentialInvalid(PermissionError):
    pass


class PostgresControlPlaneStore:
    """Tenant-aware metadata store; never point this at the runner database."""

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise ValueError("A dedicated control-plane Postgres URL is required.")
        self.dsn = dsn.strip()
        self._initialize_schema()

    @staticmethod
    def database_target(dsn: str) -> tuple[str, str, str]:
        """Return the configured Postgres host, port, and database without credentials."""
        try:
            from psycopg.conninfo import conninfo_to_dict
        except ImportError as exc:
            raise RuntimeError(
                "Control-plane storage requires the optional 'postgres' package extra."
            ) from exc
        values = conninfo_to_dict(dsn)
        database = values.get("dbname") or values.get("user") or ""
        return (
            (values.get("host") or "").casefold(),
            values.get("port") or "5432",
            database.casefold(),
        )

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "Control-plane storage requires the optional 'postgres' package extra."
            ) from exc
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _initialize_schema(self) -> None:
        migrations = {
            1: (
                """CREATE TABLE IF NOT EXISTS cp_workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
                slug TEXT NOT NULL UNIQUE CHECK(length(slug) BETWEEN 3 AND 60),
                created_at TIMESTAMPTZ NOT NULL,
                created_by TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS cp_memberships (
                workspace_id TEXT NOT NULL REFERENCES cp_workspaces(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Owner','Admin','Builder','Operator','Approver','Viewer')),
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(workspace_id, subject),
                UNIQUE(workspace_id, email)
                )""",
                "CREATE INDEX IF NOT EXISTS cp_memberships_by_subject ON cp_memberships(subject, workspace_id)",
                """CREATE TABLE IF NOT EXISTS cp_invitations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES cp_workspaces(id) ON DELETE CASCADE,
                email TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Owner','Admin','Builder','Operator','Approver','Viewer')),
                token_hash TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                accepted_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
                )""",
                """CREATE INDEX IF NOT EXISTS cp_invitations_pending
                ON cp_invitations(workspace_id, expires_at)
                WHERE accepted_at IS NULL AND revoked_at IS NULL""",
                """CREATE TABLE IF NOT EXISTS cp_audit_events (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES cp_workspaces(id) ON DELETE CASCADE,
                actor_subject TEXT NOT NULL,
                event_type TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS cp_audit_by_workspace ON cp_audit_events(workspace_id, occurred_at, id)",
            ),
            2: (
                """CREATE TABLE IF NOT EXISTS cp_runners (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES cp_workspaces(id) ON DELETE CASCADE,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
                token_hash TEXT NOT NULL UNIQUE,
                created_by TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                last_heartbeat_at TIMESTAMPTZ,
                revoked_at TIMESTAMPTZ
                )""",
                "CREATE INDEX IF NOT EXISTS cp_runners_by_workspace ON cp_runners(workspace_id, created_at, id)",
            ),
            3: (
                "ALTER TABLE cp_runners ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
                "UPDATE cp_runners SET expires_at=created_at + interval '90 days' WHERE expires_at IS NULL",
                "ALTER TABLE cp_runners ALTER COLUMN expires_at SET NOT NULL",
            ),
        }
        with self._connect() as connection:
            # Serialize DDL when control-plane instances start together.
            connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_MIGRATION_LOCK_KEY,)
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS control_plane_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
            )
            rows = connection.execute(
                "SELECT version FROM control_plane_schema_migrations ORDER BY version"
            ).fetchall()
            applied = {row["version"] for row in rows}
            if any(version > CURRENT_SCHEMA_VERSION for version in applied):
                raise RuntimeError(
                    "Control-plane database schema is newer than this application."
                )
            for version in range(1, CURRENT_SCHEMA_VERSION + 1):
                if version in applied:
                    continue
                for statement in migrations[version]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO control_plane_schema_migrations(version) VALUES (%s)",
                    (version,),
                )

    @staticmethod
    def _validate_workspace_id(workspace_id: str) -> None:
        if not _SAFE_WORKSPACE_ID.fullmatch(workspace_id):
            raise WorkspaceNotFound(workspace_id)

    def check_ready(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    @staticmethod
    def _audit(
        connection,
        *,
        workspace_id: str,
        actor_subject: str,
        event_type: str,
        target_type: str,
        target_id: str,
        now: datetime,
    ) -> None:
        connection.execute(
            """INSERT INTO cp_audit_events(
                id, workspace_id, actor_subject, event_type, target_type, target_id, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                uuid4().hex,
                workspace_id,
                actor_subject,
                event_type,
                target_type,
                target_id,
                now,
            ),
        )

    @staticmethod
    def _require_member(connection, workspace_id: str, subject: str) -> str:
        row = connection.execute(
            "SELECT role FROM cp_memberships WHERE workspace_id=%s AND subject=%s FOR SHARE",
            (workspace_id, subject),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound(workspace_id)
        return row["role"]

    @staticmethod
    def _lock_workspace(connection, workspace_id: str) -> None:
        row = connection.execute(
            "SELECT id FROM cp_workspaces WHERE id=%s FOR UPDATE", (workspace_id,)
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound(workspace_id)

    @staticmethod
    def _require_admin(
        role: str, *, allow_owner_invite: bool = False, target_role: str | None = None
    ) -> None:
        if role not in {"Owner", "Admin"}:
            raise MembershipDenied("Workspace owner or admin role is required.")
        if target_role in {"Owner", "Admin"} and not allow_owner_invite:
            raise MembershipDenied("Only an owner can grant the Owner or Admin role.")

    def create_workspace(
        self, principal: Principal, *, name: str, slug: str, now: datetime | None = None
    ) -> dict[str, object]:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        workspace_id = uuid4().hex
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO cp_workspaces(id, name, slug, created_at, created_by) VALUES (%s, %s, %s, %s, %s)",
                    (workspace_id, name, slug, timestamp, principal.subject),
                )
                connection.execute(
                    "INSERT INTO cp_memberships(workspace_id, subject, email, role, created_at) VALUES (%s, %s, %s, 'Owner', %s)",
                    (workspace_id, principal.subject, principal.email, timestamp),
                )
                self._audit(
                    connection,
                    workspace_id=workspace_id,
                    actor_subject=principal.subject,
                    event_type="workspace.created",
                    target_type="workspace",
                    target_id=workspace_id,
                    now=timestamp,
                )
        except Exception as exc:
            try:
                import psycopg

                if isinstance(exc, psycopg.errors.UniqueViolation):
                    raise WorkspaceConflict(
                        "That workspace slug is already in use."
                    ) from exc
            except ImportError:
                pass
            raise
        return {
            "id": workspace_id,
            "name": name,
            "slug": slug,
            "role": "Owner",
            "created_at": timestamp.isoformat(),
        }

    def list_workspaces(self, subject: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT w.id, w.name, w.slug, m.role, w.created_at
                   FROM cp_workspaces w JOIN cp_memberships m ON m.workspace_id=w.id
                   WHERE m.subject=%s ORDER BY w.name, w.id""",
                (subject,),
            ).fetchall()
        return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]

    def get_workspace(
        self, workspace_id: str, subject: str
    ) -> dict[str, object] | None:
        self._validate_workspace_id(workspace_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT w.id, w.name, w.slug, m.role, w.created_at
                   FROM cp_workspaces w JOIN cp_memberships m ON m.workspace_id=w.id
                   WHERE w.id=%s AND m.subject=%s""",
                (workspace_id, subject),
            ).fetchone()
        if row is None:
            return None
        return {**row, "created_at": row["created_at"].isoformat()}

    def list_members(self, workspace_id: str, subject: str) -> list[dict[str, object]]:
        self._validate_workspace_id(workspace_id)
        with self._connect() as connection:
            self._require_member(connection, workspace_id, subject)
            rows = connection.execute(
                """SELECT subject, email, role, created_at FROM cp_memberships
                   WHERE workspace_id=%s ORDER BY email, subject""",
                (workspace_id,),
            ).fetchall()
        return [{**row, "created_at": row["created_at"].isoformat()} for row in rows]

    def create_invitation(
        self,
        workspace_id: str,
        principal: Principal,
        *,
        email: str,
        role: WorkspaceRole,
        token: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> dict[str, object]:
        self._validate_workspace_id(workspace_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        expires_at = expires_at.astimezone(UTC)
        if (
            not token
            or len(token) < 32
            or expires_at <= timestamp
            or expires_at > timestamp + timedelta(days=7)
        ):
            raise ValueError("Invitation token and expiry are invalid.")
        invitation_id = uuid4().hex
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            self._lock_workspace(connection, workspace_id)
            actor_role = self._require_member(
                connection, workspace_id, principal.subject
            )
            self._require_admin(
                actor_role, allow_owner_invite=actor_role == "Owner", target_role=role
            )
            existing = connection.execute(
                "SELECT 1 FROM cp_memberships WHERE workspace_id=%s AND email=%s",
                (workspace_id, email),
            ).fetchone()
            if existing:
                raise WorkspaceConflict(
                    "That email already belongs to a workspace member."
                )
            connection.execute(
                """UPDATE cp_invitations SET revoked_at=%s
                   WHERE workspace_id=%s AND email=%s AND accepted_at IS NULL AND revoked_at IS NULL""",
                (timestamp, workspace_id, email),
            )
            connection.execute(
                """INSERT INTO cp_invitations(
                    id, workspace_id, email, role, token_hash, created_by, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    invitation_id,
                    workspace_id,
                    email,
                    role,
                    token_hash,
                    principal.subject,
                    timestamp,
                    expires_at,
                ),
            )
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=principal.subject,
                event_type="invitation.created",
                target_type="invitation",
                target_id=invitation_id,
                now=timestamp,
            )
        return {
            "id": invitation_id,
            "workspace_id": workspace_id,
            "email": email,
            "role": role,
            "expires_at": expires_at.astimezone(UTC).isoformat(),
            "accepted_at": None,
            "revoked_at": None,
        }

    def list_invitations(
        self, workspace_id: str, subject: str
    ) -> list[dict[str, object]]:
        self._validate_workspace_id(workspace_id)
        with self._connect() as connection:
            role = self._require_member(connection, workspace_id, subject)
            self._require_admin(role)
            rows = connection.execute(
                """SELECT id, email, role, created_by, created_at, expires_at, accepted_at, revoked_at
                   FROM cp_invitations WHERE workspace_id=%s ORDER BY created_at DESC, id""",
                (workspace_id,),
            ).fetchall()
        return [
            {
                **row,
                **{
                    key: row[key].isoformat() if row[key] is not None else None
                    for key in ("created_at", "expires_at", "accepted_at", "revoked_at")
                },
            }
            for row in rows
        ]

    def revoke_invitation(
        self,
        workspace_id: str,
        invitation_id: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> None:
        self._validate_workspace_id(workspace_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            self._lock_workspace(connection, workspace_id)
            actor_role = self._require_member(
                connection, workspace_id, principal.subject
            )
            self._require_admin(actor_role)
            cursor = connection.execute(
                """UPDATE cp_invitations SET revoked_at=%s
                   WHERE id=%s AND workspace_id=%s AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>%s""",
                (timestamp, invitation_id, workspace_id, timestamp),
            )
            if cursor.rowcount != 1:
                raise InvitationInvalid(
                    "Invitation is missing, expired, accepted, or already revoked."
                )
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=principal.subject,
                event_type="invitation.revoked",
                target_type="invitation",
                target_id=invitation_id,
                now=timestamp,
            )

    def redeem_invitation(
        self,
        invitation_id: str,
        token: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if not _SAFE_WORKSPACE_ID.fullmatch(invitation_id):
            raise InvitationInvalid("Invitation is invalid or expired.")
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            lookup = connection.execute(
                "SELECT workspace_id FROM cp_invitations WHERE id=%s", (invitation_id,)
            ).fetchone()
            if lookup is None:
                raise InvitationInvalid("Invitation is invalid or expired.")
            self._lock_workspace(connection, lookup["workspace_id"])
            invitation = connection.execute(
                "SELECT * FROM cp_invitations WHERE id=%s FOR UPDATE", (invitation_id,)
            ).fetchone()
            if invitation is None:
                raise InvitationInvalid("Invitation is invalid or expired.")
            if (
                not hmac.compare_digest(invitation["token_hash"], supplied_hash)
                or invitation["accepted_at"] is not None
                or invitation["revoked_at"] is not None
                or invitation["expires_at"] <= timestamp
                or invitation["email"] != principal.email
            ):
                raise InvitationInvalid(
                    "Invitation is invalid, expired, or does not match this verified email."
                )
            already_member = connection.execute(
                "SELECT 1 FROM cp_memberships WHERE workspace_id=%s AND (subject=%s OR email=%s)",
                (invitation["workspace_id"], principal.subject, principal.email),
            ).fetchone()
            if already_member:
                raise InvitationInvalid(
                    "This identity is already a member of the workspace."
                )
            connection.execute(
                """INSERT INTO cp_memberships(workspace_id, subject, email, role, created_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    invitation["workspace_id"],
                    principal.subject,
                    principal.email,
                    invitation["role"],
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE cp_invitations SET accepted_at=%s WHERE id=%s",
                (timestamp, invitation_id),
            )
            self._audit(
                connection,
                workspace_id=invitation["workspace_id"],
                actor_subject=principal.subject,
                event_type="invitation.redeemed",
                target_type="invitation",
                target_id=invitation_id,
                now=timestamp,
            )
        return {
            "workspace_id": invitation["workspace_id"],
            "role": invitation["role"],
        }

    def change_member_role(
        self,
        workspace_id: str,
        target_subject: str,
        principal: Principal,
        *,
        role: WorkspaceRole,
        now: datetime | None = None,
    ) -> None:
        self._validate_workspace_id(workspace_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            self._lock_workspace(connection, workspace_id)
            actor_role = self._require_member(
                connection, workspace_id, principal.subject
            )
            self._require_admin(
                actor_role,
                allow_owner_invite=actor_role == "Owner",
                target_role=role,
            )
            target = connection.execute(
                "SELECT role FROM cp_memberships WHERE workspace_id=%s AND subject=%s FOR UPDATE",
                (workspace_id, target_subject),
            ).fetchone()
            if target is None:
                raise WorkspaceNotFound(workspace_id)
            if target["role"] == "Owner" and role != "Owner":
                owners = connection.execute(
                    "SELECT COUNT(*) AS count FROM cp_memberships WHERE workspace_id=%s AND role='Owner'",
                    (workspace_id,),
                ).fetchone()["count"]
                if owners <= 1:
                    raise WorkspaceConflict(
                        "A workspace must retain at least one owner."
                    )
            if target["role"] in {"Owner", "Admin"} and actor_role != "Owner":
                raise MembershipDenied(
                    "Only an owner can change an owner or admin role."
                )
            connection.execute(
                "UPDATE cp_memberships SET role=%s WHERE workspace_id=%s AND subject=%s",
                (role, workspace_id, target_subject),
            )
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=principal.subject,
                event_type="membership.role_changed",
                target_type="membership",
                target_id=target_subject,
                now=timestamp,
            )

    def remove_member(
        self,
        workspace_id: str,
        target_subject: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> None:
        self._validate_workspace_id(workspace_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            self._lock_workspace(connection, workspace_id)
            actor_role = self._require_member(
                connection, workspace_id, principal.subject
            )
            self._require_admin(actor_role)
            target = connection.execute(
                "SELECT role FROM cp_memberships WHERE workspace_id=%s AND subject=%s FOR UPDATE",
                (workspace_id, target_subject),
            ).fetchone()
            if target is None:
                raise WorkspaceNotFound(workspace_id)
            if target["role"] in {"Owner", "Admin"} and actor_role != "Owner":
                raise MembershipDenied("Only an owner can remove an owner or admin.")
            if target["role"] == "Owner":
                owners = connection.execute(
                    "SELECT COUNT(*) AS count FROM cp_memberships WHERE workspace_id=%s AND role='Owner'",
                    (workspace_id,),
                ).fetchone()["count"]
                if owners <= 1:
                    raise WorkspaceConflict(
                        "Transfer ownership before removing the last owner."
                    )
            connection.execute(
                "DELETE FROM cp_memberships WHERE workspace_id=%s AND subject=%s",
                (workspace_id, target_subject),
            )
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=principal.subject,
                event_type="membership.removed",
                target_type="membership",
                target_id=target_subject,
                now=timestamp,
            )

    def list_audit_events(
        self, workspace_id: str, subject: str, *, limit: int = 100
    ) -> list[dict[str, object]]:
        self._validate_workspace_id(workspace_id)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        with self._connect() as connection:
            role = self._require_member(connection, workspace_id, subject)
            self._require_admin(role)
            rows = connection.execute(
                """SELECT id, actor_subject, event_type, target_type, target_id, occurred_at
                   FROM cp_audit_events WHERE workspace_id=%s
                   ORDER BY occurred_at DESC, id LIMIT %s""",
                (workspace_id, limit),
            ).fetchall()
        return [{**row, "occurred_at": row["occurred_at"].isoformat()} for row in rows]

    def create_runner_enrollment(
        self,
        workspace_id: str,
        principal: Principal,
        *,
        name: str,
        token: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        self._validate_workspace_id(workspace_id)
        display_name = name.strip()
        if not display_name or len(display_name) > 100 or len(token) < 32:
            raise ValueError("Runner name or enrollment token is invalid.")
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        expiration = (expires_at or timestamp + timedelta(days=90)).astimezone(UTC)
        if expiration <= timestamp or expiration > timestamp + timedelta(days=90):
            raise ValueError("Runner credential expiry must be within 90 days.")
        runner_id = uuid4().hex
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            self._lock_workspace(connection, workspace_id)
            actor_role = self._require_member(
                connection, workspace_id, principal.subject
            )
            self._require_admin(actor_role)
            connection.execute(
                """INSERT INTO cp_runners(
                    id, workspace_id, name, token_hash, created_by, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    runner_id,
                    workspace_id,
                    display_name,
                    token_hash,
                    principal.subject,
                    timestamp,
                    expiration,
                ),
            )
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=principal.subject,
                event_type="runner.enrollment_created",
                target_type="runner",
                target_id=runner_id,
                now=timestamp,
            )
        return {
            "id": runner_id,
            "workspace_id": workspace_id,
            "name": display_name,
            "created_by": principal.subject,
            "created_at": timestamp.isoformat(),
            "expires_at": expiration.isoformat(),
            "last_heartbeat_at": None,
            "revoked_at": None,
        }

    def list_runners(
        self, workspace_id: str, subject: str
    ) -> list[dict[str, object]]:
        self._validate_workspace_id(workspace_id)
        with self._connect() as connection:
            self._require_member(connection, workspace_id, subject)
            rows = connection.execute(
                """SELECT id, name, created_by, created_at, expires_at,
                          last_heartbeat_at, revoked_at
                   FROM cp_runners WHERE workspace_id=%s ORDER BY created_at, id""",
                (workspace_id,),
            ).fetchall()
        return [
            {
                **row,
                **{
                    key: row[key].isoformat() if row[key] is not None else None
                    for key in (
                        "created_at",
                        "expires_at",
                        "last_heartbeat_at",
                        "revoked_at",
                    )
                },
            }
            for row in rows
        ]

    def authorize_runner_session(
        self,
        workspace_id: str,
        runner_id: str,
        subject: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Read the role and runner state together before issuing a delegated token."""
        self._validate_workspace_id(workspace_id)
        if not _SAFE_WORKSPACE_ID.fullmatch(runner_id):
            raise WorkspaceNotFound(workspace_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT m.role, r.expires_at, r.revoked_at
                   FROM cp_memberships AS m
                   JOIN cp_runners AS r ON r.workspace_id=m.workspace_id
                   WHERE m.workspace_id=%s AND m.subject=%s AND r.id=%s
                   FOR SHARE OF m, r""",
                (workspace_id, subject, runner_id),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["expires_at"] <= timestamp
            ):
                raise WorkspaceNotFound(workspace_id)
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=subject,
                event_type="runner.session_issued",
                target_type="runner",
                target_id=runner_id,
                now=timestamp,
            )
            return row["role"]

    def authorize_runner_action(
        self,
        workspace_id: str,
        runner_id: str,
        subject: str,
        action: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Recheck membership and runner state immediately before an external write."""
        if action != "publish_ticket_reply":
            raise MembershipDenied("Workspace action is not permitted.")
        self._validate_workspace_id(workspace_id)
        if not _SAFE_WORKSPACE_ID.fullmatch(runner_id):
            raise WorkspaceNotFound(workspace_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT m.role, r.expires_at, r.revoked_at
                   FROM cp_memberships AS m
                   JOIN cp_runners AS r ON r.workspace_id=m.workspace_id
                   WHERE m.workspace_id=%s AND m.subject=%s AND r.id=%s
                   FOR SHARE OF m, r""",
                (workspace_id, subject, runner_id),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["expires_at"] <= timestamp
            ):
                raise WorkspaceNotFound(workspace_id)
            if row["role"] not in {"Owner", "Admin", "Approver"}:
                raise MembershipDenied("Current workspace role cannot publish replies.")
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=subject,
                event_type="runner.external_write_authorized",
                target_type="runner",
                target_id=runner_id,
                now=timestamp,
            )
            return row["role"]

    def revoke_runner(
        self,
        workspace_id: str,
        runner_id: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> None:
        self._validate_workspace_id(workspace_id)
        if not _SAFE_WORKSPACE_ID.fullmatch(runner_id):
            raise WorkspaceNotFound(runner_id)
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        with self._connect() as connection:
            self._lock_workspace(connection, workspace_id)
            actor_role = self._require_member(
                connection, workspace_id, principal.subject
            )
            self._require_admin(actor_role)
            cursor = connection.execute(
                """UPDATE cp_runners SET revoked_at=%s
                   WHERE id=%s AND workspace_id=%s AND revoked_at IS NULL""",
                (timestamp, runner_id, workspace_id),
            )
            if cursor.rowcount != 1:
                raise WorkspaceNotFound(runner_id)
            self._audit(
                connection,
                workspace_id=workspace_id,
                actor_subject=principal.subject,
                event_type="runner.revoked",
                target_type="runner",
                target_id=runner_id,
                now=timestamp,
            )

    def record_runner_heartbeat(
        self,
        runner_id: str,
        token: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        if not _SAFE_WORKSPACE_ID.fullmatch(runner_id) or len(token) < 32:
            raise RunnerCredentialInvalid("Runner credential is invalid.")
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        supplied_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, expires_at, revoked_at FROM cp_runners WHERE id=%s FOR UPDATE",
                (runner_id,),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["expires_at"] <= timestamp
                or not hmac.compare_digest(row["token_hash"], supplied_hash)
            ):
                raise RunnerCredentialInvalid("Runner credential is invalid.")
            connection.execute(
                "UPDATE cp_runners SET last_heartbeat_at=%s WHERE id=%s",
                (timestamp, runner_id),
            )
        return {"runner_id": runner_id, "last_heartbeat_at": timestamp.isoformat()}
