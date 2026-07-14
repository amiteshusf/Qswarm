"""API schemas for automation sessions (Sprint 2 control plane)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.automation_engine.engine_models import EngineCapability


class AutomationSessionCreateRequest(BaseModel):
    """Create a session and backing ``AutomationJob`` (1:1 for v1)."""

    approved_case_id: str = Field(..., min_length=1, max_length=512)
    created_by: str = Field(..., min_length=1, max_length=256)
    coding_engine: str = Field(default="stub", max_length=64)
    source_system: str | None = Field(default=None, max_length=64)
    source_reference: str | None = Field(default=None, max_length=512)
    repo_id: str | None = Field(default=None, max_length=256)
    repo_owner: str | None = Field(default=None, max_length=256)
    repo_name: str | None = Field(default=None, max_length=256)
    repo_path: str | None = Field(default=None, max_length=1024)
    base_branch: str = Field(default="main", max_length=256)
    workflow_run_id: uuid.UUID | None = None
    case_title: str | None = Field(default=None, max_length=512)
    case_description: str | None = None
    preconditions: list[str] | None = None
    steps: list[str] | None = None
    expected_results: list[str] | None = None
    repository_connection_id: uuid.UUID | None = Field(
        default=None,
        description="Optional saved link to RepositoryConnection for hosted workspace clone on start.",
    )


class AutomationSessionSummaryResponse(BaseModel):
    id: str
    source_system: str | None
    source_reference: str | None
    automation_job_id: str | None
    repo_owner: str | None
    repo_name: str | None
    repo_path: str | None
    repository_connection_id: str | None = None
    base_branch: str
    coding_engine: str
    status: str
    current_round_number: int
    approved_case_id: str | None
    workflow_run_id: str | None
    created_by: str
    created_at: str | None
    updated_at: str | None
    job_status: str | None = None


class AutomationSessionStartRequest(BaseModel):
    actor_id: str | None = Field(default=None, max_length=256)
    repository_connection_id: uuid.UUID | None = Field(
        default=None,
        description="Overrides session.repository_connection_id for this start (clone source).",
    )


class AutomationSessionStartResponse(BaseModel):
    id: str
    status: str
    job_status: str | None
    message: str = "Session start pipeline finished."
    accepted_async: bool = False


class AutomationSessionRevisionBody(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)
    instruction_text: str = Field(..., min_length=1, max_length=20000)
    target_scope: str | None = Field(default=None, max_length=512)


class AutomationSessionManualAckBody(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)
    note: str = Field(..., min_length=1, max_length=5000)


class AutomationSessionApproveBody(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)


class AutomationSessionSimpleResponse(BaseModel):
    id: str
    status: str
    job_status: str | None
    message: str
    accepted_async: bool = False


class AutomationRevisionRoundsListResponse(BaseModel):
    items: list[dict[str, Any]]


class AutomationPlanVersionsListResponse(BaseModel):
    items: list[dict[str, Any]]


class AutomationPatchVersionsListResponse(BaseModel):
    items: list[dict[str, Any]]


class AutomationExecutionAttemptsListResponse(BaseModel):
    items: list[dict[str, Any]]


class AutomationReviewRequestsListResponse(BaseModel):
    items: list[dict[str, Any]]


class EngineCapabilitiesListResponse(BaseModel):
    items: list[EngineCapability]
