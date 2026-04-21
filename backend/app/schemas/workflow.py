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


class JiraGeneratedDraftCaseResponse(BaseModel):
    """One persisted Jira draft Task created during Sprint 1 publish."""

    id: uuid.UUID
    parent_jira_issue_key: str
    generated_jira_issue_key: str | None
    title: str
    case_type: str
    reviewer_account_id: str | None
    external_system: str
    publish_status: str
    link_status: str
    assignment_status: str
    error_detail: str | None
    case_index: int = 0
    internal_sync_version: int | None = None
    jira_sync_status: str | None = None
    last_sync_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowJiraDraftTestCasesResponse(BaseModel):
    workflow_run_id: uuid.UUID
    items: list[JiraGeneratedDraftCaseResponse]


class TestDesignFeedbackRequest(BaseModel):
    actor_id: str = Field(..., min_length=1, examples=["qa.lead"])
    feedback_text: str = Field(..., min_length=1, examples=["Add more negative scenarios and make steps more detailed."])
    target_scope: str | None = Field(default=None, max_length=128, examples=["all"])


class TestDesignEvolutionResponse(BaseModel):
    ok: bool = True
    workflow_run_id: str
    new_version_number: int
    action: str
    message: str


class TestDesignVersionItem(BaseModel):
    id: str
    artifact_id: str
    version_number: int
    parent_version_id: str | None
    version_action: str
    source_feedback_id: str | None
    is_current: bool
    created_by: str
    created_at: str
    notes: str | None = None


class TestDesignVersionsListResponse(BaseModel):
    workflow_run_id: uuid.UUID
    items: list[TestDesignVersionItem]


class TestDesignFeedbackItem(BaseModel):
    id: str
    reviewed_version_id: str | None
    action_type: str
    feedback_text: str
    actor_id: str
    target_scope: str | None
    error_detail: str | None
    created_at: str


class TestDesignFeedbackListResponse(BaseModel):
    workflow_run_id: uuid.UUID
    items: list[TestDesignFeedbackItem]


class JiraReviewIssueInfoResponse(BaseModel):
    workflow_run_id: uuid.UUID
    parent_jira_issue_key: str | None = None
    review_jira_issue_key: str | None = None
    publish_status: str | None = None
    last_sync_error: str | None = None
    artifact_id: str | None = None


class JiraReviewCommentEventItem(BaseModel):
    id: str
    review_issue_key: str
    jira_comment_id: str
    author_account_id: str | None
    raw_comment_text: str
    parsed_action_type: str
    target_scope: str | None
    reviewed_version_id: str | None
    processed_status: str
    response_comment_id: str | None
    error_detail: str | None
    created_feedback_id: str | None
    created_at: str


class JiraReviewCommentEventsListResponse(BaseModel):
    workflow_run_id: uuid.UUID
    items: list[JiraReviewCommentEventItem]


class JiraReviewProcessCommentsResponse(BaseModel):
    ok: bool = True
    workflow_run_id: str
    processed_count: int
    skipped_duplicates: int = 0
    errors: list[str] = Field(default_factory=list)
