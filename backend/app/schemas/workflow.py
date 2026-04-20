"""Workflow run API schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowRunCreateRequest(BaseModel):
    jira_issue_key: str = Field(..., min_length=1, examples=["QSW-101"])
    initiated_by: str = Field(..., min_length=1, examples=["jdoe"])


class WorkflowRunResponse(BaseModel):
    id: uuid.UUID
    jira_story_id: uuid.UUID | None
    jira_issue_key: str | None = None
    workflow_name: str
    status: str
    current_step: str | None
    initiated_by: str
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowStartResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    message: str = Field(default="Workflow executed through approval gate.")
