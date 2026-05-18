"""Schemas for repository connections and branch policies."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class RepositoryConnectionCreateRequest(BaseModel):
    provider: str = Field(..., max_length=32)
    display_name: str = Field(..., min_length=1, max_length=256)
    owner_or_org: str = Field(..., min_length=1, max_length=256)
    repo_name: str = Field(..., min_length=1, max_length=256)
    created_by: str = Field(..., min_length=1, max_length=256)
    project_or_workspace: str | None = Field(default=None, max_length=256)
    clone_url: str | None = Field(default=None, max_length=1024)
    default_branch: str = Field(default="main", max_length=256)
    auth_type: str = Field(default="github_pat_env", max_length=64)
    credential_reference: str | None = Field(default=None, max_length=256)
    is_active: bool = True


class RepositoryConnectionPatchRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=256)
    owner_or_org: str | None = Field(default=None, max_length=256)
    repo_name: str | None = Field(default=None, max_length=256)
    project_or_workspace: str | None = None
    clone_url: str | None = None
    default_branch: str | None = Field(default=None, max_length=256)
    auth_type: str | None = Field(default=None, max_length=64)
    credential_reference: str | None = None
    is_active: bool | None = None


class RepositoryConnectionResponse(BaseModel):
    id: str
    provider: str
    display_name: str
    owner_or_org: str
    repo_name: str
    project_or_workspace: str | None
    clone_url: str | None
    default_branch: str
    auth_type: str
    credential_reference: str | None
    is_active: bool
    created_by: str
    created_at: str | None
    updated_at: str | None


class RepositoryConnectionsListResponse(BaseModel):
    items: list[RepositoryConnectionResponse]


class BranchPolicyCreateRequest(BaseModel):
    base_branch_default: str = Field(default="main", max_length=256)
    branch_naming_pattern: str = Field(default="qswarm/{session_id}", max_length=512)
    allow_session_override: bool = True
    commit_message_template: str | None = Field(default=None, max_length=512)
    pr_title_template: str | None = Field(default=None, max_length=512)
    pr_body_template: str | None = None
    default_reviewers_json: dict[str, Any] | None = None
    default_labels_json: list[Any] | None = None


class BranchPolicyPatchRequest(BaseModel):
    base_branch_default: str | None = Field(default=None, max_length=256)
    branch_naming_pattern: str | None = Field(default=None, max_length=512)
    allow_session_override: bool | None = None
    commit_message_template: str | None = None
    pr_title_template: str | None = None
    pr_body_template: str | None = None
    default_reviewers_json: dict[str, Any] | None = None
    default_labels_json: list[Any] | None = None


class BranchPolicyResponse(BaseModel):
    id: str
    repository_connection_id: str
    base_branch_default: str
    branch_naming_pattern: str
    allow_session_override: bool
    commit_message_template: str | None
    pr_title_template: str | None
    pr_body_template: str | None
    default_reviewers_json: dict[str, Any] | None
    default_labels_json: list[Any] | None
    created_at: str | None
    updated_at: str | None


class AutomationSessionCreatePrBody(BaseModel):
    actor_id: str = Field(..., min_length=1, max_length=256)
    repository_connection_id: uuid.UUID
    target_branch: str | None = Field(default=None, max_length=256)
    source_branch: str | None = Field(default=None, max_length=512)
    title_override: str | None = Field(default=None, max_length=512)
    body_override: str | None = None


class AutomationSessionCreatePrResponse(BaseModel):
    id: str
    status: str
    job_status: str | None
    code_review_request_id: str
    external_url: str | None
    external_id: str | None
    message: str


class CodeReviewRequestsListResponse(BaseModel):
    items: list[dict[str, Any]]
