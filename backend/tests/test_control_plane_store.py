from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from app.control_plane.auth import Principal
from app.control_plane.store import (
    InvitationInvalid,
    MembershipDenied,
    PostgresControlPlaneStore,
    RunnerCredentialInvalid,
    WorkspaceConflict,
    WorkspaceNotFound,
)


@pytest.fixture
def store() -> PostgresControlPlaneStore:
    dsn = os.getenv("POSTGRES_TEST_URL", "").strip()
    if not dsn:
        pytest.skip("Control-plane Postgres tests require POSTGRES_TEST_URL.")
    pytest.importorskip("psycopg")
    value = PostgresControlPlaneStore(dsn)
    with value._connect() as connection:
        connection.execute(
            "TRUNCATE TABLE cp_audit_events, cp_invitations, cp_memberships, cp_workspaces CASCADE"
        )
    return value


@pytest.fixture
def owner() -> Principal:
    return Principal(subject="oidc|owner", email="owner@example.test")


def test_workspace_memberships_are_isolated_and_create_owner_audit(
    store, owner
) -> None:
    first = store.create_workspace(owner, name="Support Team", slug="support-team")
    second = store.create_workspace(
        Principal(subject="oidc|other", email="other@example.test"),
        name="Other Team",
        slug="other-team",
    )

    assert store.get_workspace(str(first["id"]), owner.subject)["role"] == "Owner"
    assert store.get_workspace(str(second["id"]), owner.subject) is None
    assert [item["id"] for item in store.list_workspaces(owner.subject)] == [
        first["id"]
    ]
    with pytest.raises(WorkspaceNotFound):
        store.list_members(str(second["id"]), owner.subject)
    assert (
        store.list_audit_events(str(first["id"]), owner.subject)[0]["event_type"]
        == "workspace.created"
    )


def test_invitation_is_hashed_one_time_email_bound_and_creates_assigned_role(
    store, owner
) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    invitation = store.create_invitation(
        workspace_id,
        owner,
        email="builder@example.test",
        role="Builder",
        token=token,
        expires_at=now + timedelta(hours=4),
        now=now,
    )
    invitation_id = str(invitation["id"])

    with pytest.raises(InvitationInvalid):
        store.redeem_invitation(
            invitation_id,
            token,
            Principal(subject="oidc|wrong-email", email="wrong@example.test"),
            now=now + timedelta(seconds=1),
        )

    result = store.redeem_invitation(
        invitation_id,
        token,
        Principal(subject="oidc|builder", email="builder@example.test"),
        now=now + timedelta(seconds=1),
    )
    assert result == {"workspace_id": workspace_id, "role": "Builder"}
    with pytest.raises(InvitationInvalid):
        store.redeem_invitation(
            invitation_id,
            token,
            Principal(subject="oidc|builder", email="builder@example.test"),
            now=now + timedelta(seconds=2),
        )
    assert {
        item["subject"] for item in store.list_members(workspace_id, owner.subject)
    } == {
        owner.subject,
        "oidc|builder",
    }


def test_invitation_expiry_revocation_and_admin_owner_grant_policy(
    store, owner
) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    now = datetime.now(UTC)
    expired_token = secrets.token_urlsafe(32)
    expired = store.create_invitation(
        workspace_id,
        owner,
        email="expired@example.test",
        role="Viewer",
        token=expired_token,
        expires_at=now - timedelta(seconds=1),
        now=now - timedelta(hours=1),
    )
    with pytest.raises(InvitationInvalid):
        store.redeem_invitation(
            str(expired["id"]),
            expired_token,
            Principal(subject="oidc|expired", email="expired@example.test"),
            now=now,
        )

    admin_token = secrets.token_urlsafe(32)
    admin_invite = store.create_invitation(
        workspace_id,
        owner,
        email="admin@example.test",
        role="Admin",
        token=admin_token,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    admin = Principal(subject="oidc|admin", email="admin@example.test")
    store.redeem_invitation(str(admin_invite["id"]), admin_token, admin, now=now)
    with pytest.raises(MembershipDenied):
        store.create_invitation(
            workspace_id,
            admin,
            email="future-owner@example.test",
            role="Owner",
            token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(hours=1),
            now=now,
        )

    with pytest.raises(MembershipDenied):
        store.create_invitation(
            workspace_id,
            admin,
            email="second-future-owner@example.test",
            role="Owner",
            token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(hours=1),
            now=now,
        )

    revoke_token = secrets.token_urlsafe(32)
    revoke = store.create_invitation(
        workspace_id,
        owner,
        email="revoke@example.test",
        role="Viewer",
        token=revoke_token,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    store.revoke_invitation(workspace_id, str(revoke["id"]), owner, now=now)
    with pytest.raises(InvitationInvalid):
        store.redeem_invitation(
            str(revoke["id"]),
            revoke_token,
            Principal(subject="oidc|revoke", email="revoke@example.test"),
            now=now + timedelta(seconds=1),
        )


def test_only_owner_can_grant_owner_and_last_owner_cannot_be_removed(
    store, owner
) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    now = datetime.now(UTC)
    admin = Principal(subject="oidc|admin", email="admin@example.test")
    invite_token = secrets.token_urlsafe(32)
    invitation = store.create_invitation(
        workspace_id,
        owner,
        email=admin.email,
        role="Admin",
        token=invite_token,
        expires_at=now + timedelta(hours=2),
        now=now,
    )
    store.redeem_invitation(str(invitation["id"]), invite_token, admin, now=now)
    with pytest.raises(MembershipDenied):
        store.change_member_role(
            workspace_id, admin.subject, admin, role="Owner", now=now
        )
    with pytest.raises(MembershipDenied):
        store.create_invitation(
            workspace_id,
            admin,
            email="second-admin@example.test",
            role="Admin",
            token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(hours=1),
            now=now,
        )
    with pytest.raises(WorkspaceConflict):
        store.remove_member(workspace_id, owner.subject, owner, now=now)

    store.change_member_role(workspace_id, admin.subject, owner, role="Owner", now=now)
    store.remove_member(workspace_id, owner.subject, admin, now=now)
    assert [
        item["subject"] for item in store.list_members(workspace_id, admin.subject)
    ] == [admin.subject]


def test_runner_enrollment_keeps_only_token_hash_and_revocation_stops_heartbeats(
    store, owner
) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)

    runner = store.create_runner_enrollment(
        workspace_id, owner, name="Private runner", token=token, now=now
    )
    runner_id = str(runner["id"])
    listed = store.list_runners(workspace_id, owner.subject)
    assert listed[0]["id"] == runner_id
    assert "token" not in listed[0]
    assert token not in repr(listed)

    with pytest.raises(RunnerCredentialInvalid):
        store.record_runner_heartbeat(
            runner_id, secrets.token_urlsafe(32), now=now + timedelta(seconds=5)
        )

    heartbeat = store.record_runner_heartbeat(
        runner_id, token, now=now + timedelta(seconds=10)
    )
    assert heartbeat["runner_id"] == runner_id
    assert listed[0]["last_heartbeat_at"] is None
    store.revoke_runner(
        workspace_id, runner_id, owner, now=now + timedelta(seconds=20)
    )
    with pytest.raises(RunnerCredentialInvalid):
        store.record_runner_heartbeat(
            runner_id, token, now=now + timedelta(seconds=30)
        )
    audit = store.list_audit_events(workspace_id, owner.subject)
    assert [item["event_type"] for item in audit[:2]] == [
        "runner.revoked",
        "runner.enrollment_created",
    ]


def test_runner_enrollment_requires_workspace_admin(store, owner) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    now = datetime.now(UTC)
    builder = Principal(subject="oidc|builder", email="builder@example.test")
    token = secrets.token_urlsafe(32)
    invite = store.create_invitation(
        workspace_id,
        owner,
        email=builder.email,
        role="Builder",
        token=token,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    store.redeem_invitation(str(invite["id"]), token, builder, now=now)

    with pytest.raises(MembershipDenied):
        store.create_runner_enrollment(
            workspace_id,
            builder,
            name="Unauthorized runner",
            token=secrets.token_urlsafe(32),
            now=now,
        )


def test_runner_credential_expiry_rejects_late_heartbeats(store, owner) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    runner = store.create_runner_enrollment(
        workspace_id,
        owner,
        name="Short-lived runner",
        token=token,
        expires_at=now + timedelta(days=1),
        now=now,
    )

    with pytest.raises(RunnerCredentialInvalid):
        store.record_runner_heartbeat(
            str(runner["id"]), token, now=now + timedelta(days=1)
        )


def test_runner_expiry_migration_backfills_existing_v2_rows(store, owner) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    created_at = datetime(2025, 1, 1, tzinfo=UTC)
    runner = store.create_runner_enrollment(
        workspace_id,
        owner,
        name="Existing v2 runner",
        token=secrets.token_urlsafe(32),
        now=created_at,
    )

    with store._connect() as connection:
        connection.execute(
            "UPDATE cp_runners SET created_at=%s WHERE id=%s",
            (created_at, runner["id"]),
        )
        connection.execute("ALTER TABLE cp_runners DROP COLUMN expires_at")
        connection.execute(
            "DELETE FROM control_plane_schema_migrations WHERE version=3"
        )

    upgraded = PostgresControlPlaneStore(store.dsn)
    listed = upgraded.list_runners(workspace_id, owner.subject)

    assert listed[0]["expires_at"] == (created_at + timedelta(days=90)).isoformat()


def test_runner_session_uses_current_workspace_role_and_rejects_revoked_runner(
    store, owner
) -> None:
    workspace = store.create_workspace(owner, name="Support Team", slug="support-team")
    workspace_id = str(workspace["id"])
    now = datetime.now(UTC)
    viewer = Principal(subject="oidc|viewer", email="viewer@example.test")
    invitation_token = secrets.token_urlsafe(32)
    invitation = store.create_invitation(
        workspace_id,
        owner,
        email=viewer.email,
        role="Viewer",
        token=invitation_token,
        expires_at=now + timedelta(hours=1),
        now=now,
    )
    store.redeem_invitation(str(invitation["id"]), invitation_token, viewer, now=now)
    runner_token = secrets.token_urlsafe(32)
    runner = store.create_runner_enrollment(
        workspace_id,
        owner,
        name="Workspace runner",
        token=runner_token,
        expires_at=now + timedelta(days=1),
        now=now,
    )
    runner_id = str(runner["id"])

    assert store.authorize_runner_session(
        workspace_id, runner_id, viewer.subject, now=now + timedelta(seconds=1)
    ) == "Viewer"
    assert (
        store.list_audit_events(workspace_id, owner.subject)[0]["event_type"]
        == "runner.session_issued"
    )
    store.revoke_runner(workspace_id, runner_id, owner, now=now + timedelta(seconds=2))
    with pytest.raises(WorkspaceNotFound):
        store.authorize_runner_session(
            workspace_id, runner_id, viewer.subject, now=now + timedelta(seconds=3)
        )


def test_control_plane_database_target_comparison_ignores_credentials() -> None:
    pytest.importorskip("psycopg")

    control_plane = PostgresControlPlaneStore.database_target(
        "host=db.example.test port=5432 dbname=control user=control_user"
    )
    same_runner_database = PostgresControlPlaneStore.database_target(
        "host=db.example.test port=5432 dbname=control user=runner_user"
    )
    runner_database = PostgresControlPlaneStore.database_target(
        "host=db.example.test port=5432 dbname=runner user=runner_user"
    )

    assert control_plane == same_runner_database
    assert control_plane != runner_database
