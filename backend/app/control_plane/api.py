"""Authenticated metadata-only control-plane API.

Run this ASGI app separately from the ticket runner. It accepts identity and
workspace metadata only; it is not yet the UI's auth server or a runner relay.
"""

# FastAPI dependency calls in defaults are its standard injection syntax.
# ruff: noqa: B008

from __future__ import annotations

import os
import re
import secrets
import threading
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import Principal, current_principal
from .store import (
    InvitationInvalid,
    MembershipDenied,
    PostgresControlPlaneStore,
    RunnerCredentialInvalid,
    WorkspaceConflict,
    WorkspaceNotFound,
)

Role = Literal["Owner", "Admin", "Builder", "Operator", "Approver", "Viewer"]
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,58}[a-z0-9])$")


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=3, max_length=60)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Workspace name cannot be blank.")
        return value

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not _SLUG_PATTERN.fullmatch(value):
            raise ValueError(
                "Slug must use lowercase letters, numbers, and internal hyphens."
            )
        return value


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    role: Role
    expires_in_hours: int = Field(default=168, ge=1, le=168)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not _EMAIL_PATTERN.fullmatch(value):
            raise ValueError("A valid email address is required.")
        return value


class InvitationRedeem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=128)


class MemberRoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class RunnerEnrollmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=90, ge=1, le=90)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Runner name cannot be blank.")
        return value


def _slug_from_name(name: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")[:60].strip("-")
    if len(slug) < 3:
        raise HTTPException(
            status_code=422,
            detail="Provide a workspace slug with at least three characters.",
        )
    return slug


def get_control_plane_store(request: Request) -> PostgresControlPlaneStore:
    store = request.app.state.control_plane_store
    if store is not None:
        return store
    dsn = os.getenv("SUPPORT_CONTROL_PLANE_DATABASE_URL", "").strip()
    if not dsn:
        raise HTTPException(
            status_code=503, detail="Control-plane storage is not configured."
        )
    runner_dsn = os.getenv("SUPPORT_WORKFLOW_DATABASE_URL", "").strip()
    if runner_dsn:
        try:
            same_database = PostgresControlPlaneStore.database_target(dsn) == (
                PostgresControlPlaneStore.database_target(runner_dsn)
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Database separation could not be verified."
            ) from exc
        if same_database:
            raise HTTPException(
                status_code=503,
                detail="Control-plane storage must use a database separate from runner ticket data.",
            )
    with request.app.state.store_lock:
        store = request.app.state.control_plane_store
        if store is None:
            try:
                store = PostgresControlPlaneStore(dsn)
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(
                    status_code=503, detail="Control-plane storage is unavailable."
                ) from exc
            request.app.state.control_plane_store = store
    return store


def create_control_plane_app(
    store: PostgresControlPlaneStore | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Support Workflow Studio Control Plane",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.control_plane_store = store
    app.state.store_lock = threading.Lock()

    @app.exception_handler(WorkspaceNotFound)
    async def workspace_missing(_: Request, __: WorkspaceNotFound) -> Response:
        return Response(status_code=404)

    @app.exception_handler(MembershipDenied)
    async def access_denied(_: Request, exc: MembershipDenied) -> Response:
        return Response(content=str(exc), status_code=403, media_type="text/plain")

    @app.exception_handler(WorkspaceConflict)
    async def workspace_conflict(_: Request, exc: WorkspaceConflict) -> Response:
        return Response(content=str(exc), status_code=409, media_type="text/plain")

    @app.exception_handler(InvitationInvalid)
    async def invitation_invalid(_: Request, exc: InvitationInvalid) -> Response:
        return Response(content=str(exc), status_code=400, media_type="text/plain")

    @app.exception_handler(RunnerCredentialInvalid)
    async def runner_credential_invalid(
        _: Request, exc: RunnerCredentialInvalid
    ) -> Response:
        return Response(content=str(exc), status_code=401, media_type="text/plain")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready(
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, str]:
        control_store.check_ready()
        return {"status": "ready"}

    @app.get("/api/v1/me")
    def me(
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        return {
            "subject": principal.subject,
            "email": principal.email,
            "workspaces": control_store.list_workspaces(principal.subject),
        }

    @app.get("/api/v1/workspaces")
    def list_workspaces(
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> list[dict[str, object]]:
        return control_store.list_workspaces(principal.subject)

    @app.post("/api/v1/workspaces", status_code=201)
    def create_workspace(
        body: WorkspaceCreate,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        slug = body.slug or _slug_from_name(body.name)
        return control_store.create_workspace(principal, name=body.name, slug=slug)

    @app.get("/api/v1/workspaces/{workspace_id}")
    def get_workspace(
        workspace_id: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        value = control_store.get_workspace(workspace_id, principal.subject)
        if value is None:
            raise WorkspaceNotFound(workspace_id)
        return value

    @app.get("/api/v1/workspaces/{workspace_id}/members")
    def list_members(
        workspace_id: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> list[dict[str, object]]:
        return control_store.list_members(workspace_id, principal.subject)

    @app.patch("/api/v1/workspaces/{workspace_id}/members/{subject}", status_code=204)
    def change_member_role(
        workspace_id: str,
        subject: str,
        body: MemberRoleChange,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> Response:
        control_store.change_member_role(
            workspace_id, subject, principal, role=body.role
        )
        return Response(status_code=204)

    @app.delete("/api/v1/workspaces/{workspace_id}/members/{subject}", status_code=204)
    def remove_member(
        workspace_id: str,
        subject: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> Response:
        control_store.remove_member(workspace_id, subject, principal)
        return Response(status_code=204)

    @app.post("/api/v1/workspaces/{workspace_id}/invitations", status_code=201)
    def invite_member(
        workspace_id: str,
        body: InvitationCreate,
        response: Response,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        invitation = control_store.create_invitation(
            workspace_id,
            principal,
            email=body.email,
            role=body.role,
            token=token,
            expires_at=now + timedelta(hours=body.expires_in_hours),
            now=now,
        )
        return {**invitation, "token": token}

    @app.get("/api/v1/workspaces/{workspace_id}/invitations")
    def list_invitations(
        workspace_id: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> list[dict[str, object]]:
        return control_store.list_invitations(workspace_id, principal.subject)

    @app.delete(
        "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204
    )
    def revoke_invitation(
        workspace_id: str,
        invitation_id: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> Response:
        control_store.revoke_invitation(workspace_id, invitation_id, principal)
        return Response(status_code=204)

    @app.post("/api/v1/invitations/{invitation_id}/redeem")
    def redeem_invitation(
        invitation_id: str,
        body: InvitationRedeem,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        return control_store.redeem_invitation(invitation_id, body.token, principal)

    @app.get("/api/v1/workspaces/{workspace_id}/audit")
    def list_audit_events(
        workspace_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> list[dict[str, object]]:
        return control_store.list_audit_events(
            workspace_id, principal.subject, limit=limit
        )

    @app.post("/api/v1/workspaces/{workspace_id}/runners", status_code=201)
    def create_runner_enrollment(
        workspace_id: str,
        body: RunnerEnrollmentCreate,
        response: Response,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        token = secrets.token_urlsafe(32)
        runner = control_store.create_runner_enrollment(
            workspace_id,
            principal,
            name=body.name,
            token=token,
            expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days),
        )
        return {**runner, "token": token}

    @app.get("/api/v1/workspaces/{workspace_id}/runners")
    def list_runners(
        workspace_id: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> list[dict[str, object]]:
        return control_store.list_runners(workspace_id, principal.subject)

    @app.delete(
        "/api/v1/workspaces/{workspace_id}/runners/{runner_id}", status_code=204
    )
    def revoke_runner(
        workspace_id: str,
        runner_id: str,
        principal: Principal = Depends(current_principal),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> Response:
        control_store.revoke_runner(workspace_id, runner_id, principal)
        return Response(status_code=204)

    @app.post("/api/v1/runners/{runner_id}/heartbeat")
    def runner_heartbeat(
        runner_id: str,
        response: Response,
        runner_token: str = Header(
            alias="X-Runner-Token", min_length=32, max_length=128
        ),
        control_store: PostgresControlPlaneStore = Depends(get_control_plane_store),
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return control_store.record_runner_heartbeat(runner_id, runner_token)

    return app


app = create_control_plane_app()
