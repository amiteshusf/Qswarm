"""Approval API schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ApprovalDecisionRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, examples=["reviewer1"])
    notes: str | None = Field(default=None, max_length=4000)


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    workflow_run_id: uuid.UUID
    artifact_id: uuid.UUID
    status: str
    requested_by: str
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
