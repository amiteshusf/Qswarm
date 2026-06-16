"""Request bodies for /api/v1 (camelCase aliases; accepts snake_case too)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.automation_session import (
    AutomationSessionCreateRequest,
    AutomationSessionStartRequest,
)
from app.schemas.repository_connection import (
    BranchPolicyCreateRequest,
    BranchPolicyPatchRequest,
    RepositoryConnectionCreateRequest,
    RepositoryConnectionPatchRequest,
)


class UiAutomationSessionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    approved_case_id: str = Field(..., alias="approvedCaseId", min_length=1, max_length=512)
    created_by: str = Field(..., alias="createdBy", min_length=1, max_length=256)
    coding_engine: str = Field(default="stub", alias="codingEngine", max_length=64)
    source_system: str | None = Field(default=None, alias="sourceSystem", max_length=64)
    source_reference: str | None = Field(default=None, alias="sourceReference", max_length=512)
    repo_id: str | None = Field(default=None, alias="repoId", max_length=256)
    repo_owner: str | None = Field(default=None, alias="repoOwner", max_length=256)
    repo_name: str | None = Field(default=None, alias="repoName", max_length=256)
    repo_path: str | None = Field(default=None, alias="repoPath", max_length=1024)
    base_branch: str = Field(default="main", alias="baseBranch", max_length=256)
    workflow_run_id: uuid.UUID | None = Field(default=None, alias="workflowRunId")
    case_title: str | None = Field(default=None, alias="caseTitle", max_length=512)
    case_description: str | None = Field(default=None, alias="caseDescription")
    preconditions: list[str] | None = None
    steps: list[str] | None = None
    expected_results: list[str] | None = Field(default=None, alias="expectedResults")
    repository_connection_id: uuid.UUID | None = Field(default=None, alias="repositoryConnectionId")

    def to_legacy(self) -> AutomationSessionCreateRequest:
        return AutomationSessionCreateRequest.model_validate(self.model_dump(by_alias=False))


class UiAutomationSessionStart(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str | None = Field(default=None, alias="actorId", max_length=256)
    repository_connection_id: uuid.UUID | None = Field(default=None, alias="repositoryConnectionId")

    def to_legacy(self) -> AutomationSessionStartRequest:
        return AutomationSessionStartRequest.model_validate(self.model_dump(by_alias=False))


class UiAutomationSessionApprove(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(..., alias="actorId", min_length=1, max_length=256)

    def to_legacy_actor(self) -> str:
        return self.actor_id


class UiAutomationSessionRevision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(..., alias="actorId", min_length=1, max_length=256)
    instruction_text: str = Field(..., alias="instructionText", min_length=1, max_length=20000)
    target_scope: str | None = Field(default=None, alias="targetScope", max_length=512)

    def to_legacy_tuple(self) -> tuple[str, str, str | None]:
        return self.actor_id, self.instruction_text, self.target_scope


class UiAutomationSessionCreatePr(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(..., alias="actorId", min_length=1, max_length=256)
    repository_connection_id: uuid.UUID = Field(..., alias="repositoryConnectionId")
    target_branch: str | None = Field(default=None, alias="targetBranch", max_length=256)
    source_branch: str | None = Field(default=None, alias="sourceBranch", max_length=512)
    title_override: str | None = Field(default=None, alias="titleOverride", max_length=512)
    body_override: str | None = Field(default=None, alias="bodyOverride")


class UiRepositoryConnectionCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    provider: str = Field(..., max_length=32)
    display_name: str = Field(..., alias="displayName", min_length=1, max_length=256)
    owner_or_org: str = Field(..., alias="ownerOrOrg", min_length=1, max_length=256)
    repo_name: str = Field(..., alias="repoName", min_length=1, max_length=256)
    created_by: str = Field(..., alias="createdBy", min_length=1, max_length=256)
    project_or_workspace: str | None = Field(default=None, alias="projectOrWorkspace", max_length=256)
    clone_url: str | None = Field(default=None, alias="cloneUrl", max_length=1024)
    default_branch: str = Field(default="main", alias="defaultBranch", max_length=256)
    auth_type: str = Field(default="github_pat_env", alias="authType", max_length=64)
    credential_reference: str | None = Field(default=None, alias="credentialReference", max_length=256)
    is_active: bool = Field(default=True, alias="isActive")

    def to_legacy(self) -> RepositoryConnectionCreateRequest:
        return RepositoryConnectionCreateRequest.model_validate(self.model_dump(by_alias=False))


class UiRepositoryConnectionPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    display_name: str | None = Field(default=None, alias="displayName", max_length=256)
    owner_or_org: str | None = Field(default=None, alias="ownerOrOrg", max_length=256)
    repo_name: str | None = Field(default=None, alias="repoName", max_length=256)
    project_or_workspace: str | None = Field(default=None, alias="projectOrWorkspace")
    clone_url: str | None = Field(default=None, alias="cloneUrl")
    default_branch: str | None = Field(default=None, alias="defaultBranch", max_length=256)
    auth_type: str | None = Field(default=None, alias="authType", max_length=64)
    credential_reference: str | None = Field(default=None, alias="credentialReference")
    is_active: bool | None = Field(default=None, alias="isActive")

    def to_legacy(self) -> RepositoryConnectionPatchRequest:
        return RepositoryConnectionPatchRequest.model_validate(self.model_dump(by_alias=False, exclude_none=True))


class UiBranchPolicyCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    repository_connection_id: uuid.UUID = Field(..., alias="repositoryConnectionId")
    base_branch_default: str = Field(default="main", alias="baseBranchDefault", max_length=256)
    branch_naming_pattern: str = Field(
        default="qswarm/{session_id}", alias="branchNamingPattern", max_length=512
    )
    allow_session_override: bool = Field(default=True, alias="allowSessionOverride")
    commit_message_template: str | None = Field(default=None, alias="commitMessageTemplate", max_length=512)
    pr_title_template: str | None = Field(default=None, alias="prTitleTemplate", max_length=512)
    pr_body_template: str | None = Field(default=None, alias="prBodyTemplate")
    default_reviewers_json: dict[str, Any] | None = Field(default=None, alias="defaultReviewersJson")
    default_labels_json: list[Any] | None = Field(default=None, alias="defaultLabelsJson")

    def to_legacy(self) -> BranchPolicyCreateRequest:
        d = self.model_dump(by_alias=False, exclude={"repository_connection_id"})
        return BranchPolicyCreateRequest.model_validate(d)


class UiBranchPolicyPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    base_branch_default: str | None = Field(default=None, alias="baseBranchDefault", max_length=256)
    branch_naming_pattern: str | None = Field(default=None, alias="branchNamingPattern", max_length=512)
    allow_session_override: bool | None = Field(default=None, alias="allowSessionOverride")
    commit_message_template: str | None = Field(default=None, alias="commitMessageTemplate")
    pr_title_template: str | None = Field(default=None, alias="prTitleTemplate")
    pr_body_template: str | None = Field(default=None, alias="prBodyTemplate")
    default_reviewers_json: dict[str, Any] | None = Field(default=None, alias="defaultReviewersJson")
    default_labels_json: list[Any] | None = Field(default=None, alias="defaultLabelsJson")

    def to_legacy(self) -> BranchPolicyPatchRequest:
        return BranchPolicyPatchRequest.model_validate(self.model_dump(by_alias=False, exclude_none=True))
