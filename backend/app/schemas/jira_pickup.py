"""Schemas for Jira label-based Sprint 1 pickup polling."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

PICKUP_LABEL_DEFAULT = "qswarm-test-design"

PickupAction = Literal["picked_up", "skipped", "error"]

PickupSkipReason = Literal[
    "missing_label",
    "unsupported_issue_type",
    "done_status_category",
    "missing_summary",
    "duplicate_active_run",
    "too_vague",
    "jira_fetch_error",
    "workflow_create_failed",
    "workflow_start_failed",
]


class JiraPickupPollRequest(BaseModel):
    limit: int = Field(default=10, ge=1, le=100, description="Max Jira issues to fetch from search.")


class JiraPickupResultItem(BaseModel):
    issue_key: str
    action: PickupAction
    workflow_run_id: uuid.UUID | None = None
    reason: str | None = None


class JiraPickupPollResponse(BaseModel):
    ok: bool = True
    label: str
    checked: int = Field(description="Distinct issue keys evaluated after search.")
    picked_up: int
    skipped: int
    results: list[JiraPickupResultItem]
