from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CaseRecord(BaseModel):
    id: str
    title: str
    requester: str
    channel: str
    created_at: str
    priority: str
    category: str
    status: str
    body: str
    impact: str
    tags: list[str] = Field(default_factory=list)
    source: str = "demo"
    external_key: str | None = None


class StageRecord(BaseModel):
    id: str
    label: str
    status: Literal["complete", "review", "warning", "pending", "failed"]
    detail: str
    actor: str
    elapsed_ms: int = 0


class ReviewFinding(BaseModel):
    lane: str
    score: float
    verdict: str
    findings: list[str] = Field(default_factory=list)


class WorkLaneResult(BaseModel):
    lane: str
    scope: str
    finding: str
    evidence_ids: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class CouncilOption(BaseModel):
    label: str
    proposal: str
    score: float
    rationale: str = ""
    criteria: list[str] = Field(default_factory=list)


class PlanReviewRound(BaseModel):
    round: int
    origin: Literal["architect", "deterministic_fallback"] = "architect"
    critic_score: float
    score: float
    verdict: Literal["pass", "revise", "stop"]
    plan: list[str]
    findings: list[str] = Field(default_factory=list)
    missing_checks: list[str] = Field(default_factory=list)
    deterministic_findings: list[str] = Field(default_factory=list)


class WorkflowResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    case_id: str
    status: Literal["awaiting_human", "approved", "edited", "rejected", "failed"]
    provider_mode: str
    model_name: str
    started_at: str
    elapsed_ms: int
    source_snapshot_hash: str | None = None
    approval_version: int = 0
    score: float
    rounds: int
    revision_count: int = 0
    human_edited: bool = False
    candidate_count: int
    category: str
    urgency: str
    support_level: Literal["N1", "N2", "N3", "N3+"] = "N1"
    support_route_reason: str = ""
    confidence: float
    evidence: list[dict[str, str]]
    plan: list[str]
    plan_reviews: list[PlanReviewRound] = Field(default_factory=list)
    draft: str
    verifier_findings: list[str] = Field(default_factory=list)
    reviewer_findings: list[ReviewFinding]
    work_lanes: list[WorkLaneResult] = Field(default_factory=list)
    council_options: list[CouncilOption] = Field(default_factory=list)
    stages: list[StageRecord]
    human_decision: str | None = None
    ticket_comment_published: bool = False
    ticket_comment_id: str | None = None
    ticket_comment_outcome: Literal["not_started", "published", "unknown", "failed"] = (
        "not_started"
    )


class RunRequest(BaseModel):
    case_id: str
    candidate_count: int = Field(default=2, ge=1, le=3)
    include_model_council: bool = False


class DecisionRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    expected_version: int = Field(ge=0)
    edited_draft: str | None = Field(default=None, max_length=5000)


class IntakeRequest(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    requester: str = Field(min_length=2, max_length=80)
    body: str = Field(min_length=15, max_length=4000)
    impact: str = Field(default="Unknown impact", max_length=200)


class WorkspaceMetrics(BaseModel):
    open_cases: int
    total_cases: int
    workflow_runs: int
    live_model_runs: int
    drafts_passing_gate: int
    reviewer_approved_runs: int
    reviewer_edited_runs: int
    reviewer_rejected_runs: int
    public_replies_published: int
    median_triage_seconds: float | None
