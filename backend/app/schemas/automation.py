"""Automation job API schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AutomationJobCreateRequest(BaseModel):
    approved_case_id: str = Field(..., min_length=1, max_length=512)
    requested_by: str = Field(..., min_length=1, max_length=256)
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


class AutomationJobResponse(BaseModel):
    id: uuid.UUID
    approved_case_id: str
    workflow_run_id: uuid.UUID | None
    repo_id: str | None
    repo_owner: str | None = None
    repo_name: str | None = None
    repo_path: str | None
    base_branch: str
    branch_name: str | None
    requested_by: str
    status: str
    blocked_reason: str | None
    latest_attempt_number: int
    framework_type: str | None = None
    framework_summary_json: dict[str, Any] | None = None
    case_spec_json: dict[str, Any] | None = None
    repo_context_json: dict[str, Any] | None = None
    change_plan_json: dict[str, Any] | None = None
    generated_patch_json: dict[str, Any] | None = None
    execution_result_json: dict[str, Any] | None = None
    failure_analysis_json: dict[str, Any] | None = None
    repair_result_json: dict[str, Any] | None = None
    final_result_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AutomationJobListResponse(BaseModel):
    items: list[AutomationJobResponse]


class AutomationJobStartResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(
        default="Automation job start finished.",
        description="Human-readable outcome of start (scan + case + repo context).",
    )


class AutomationJobPlanResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(
        default="Change plan created successfully",
        description="Outcome of change planning (stub or future LLM provider).",
    )


class AutomationJobGenerateResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(
        default="Code generated and applied successfully",
        description="Outcome of code generation and workspace apply.",
    )


class AutomationJobExecuteResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(
        default="Execution finished.",
        description="Outcome of Playwright execution (tests pass or fail).",
    )


class AutomationJobRepairResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(
        default="Repair flow finished.",
        description="Outcome of failure analysis and optional one-step repair.",
    )


class AutomationJobApproveRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)


class AutomationJobRevisionRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)
    instruction_text: str = Field(..., min_length=1, max_length=20000)


class AutomationJobManualEditAckRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)
    note: str = Field(..., min_length=1, max_length=5000)


class AutomationJobApproveResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(default="Automation approved for PR creation.")


class AutomationJobRevisionResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(default="Revision flow finished.")


class AutomationJobManualEditAckResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(default="Manual edit acknowledgement finished.")


class AutomationJobCreatePrRequest(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=256)
    repo_owner: str | None = Field(default=None, max_length=256)
    repo_name: str | None = Field(default=None, max_length=256)


class AutomationJobCreatePrResponse(BaseModel):
    id: uuid.UUID
    status: str
    message: str = Field(default="PR creation finished.")
    pr_url: str | None = None
    pr_number: int | None = None
