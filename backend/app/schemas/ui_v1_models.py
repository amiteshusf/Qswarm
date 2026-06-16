"""Request bodies for /api/v1 (camelCase aliases; accepts snake_case too)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    """
    Request body aligned with Qswarm-UI ``repoConnectionInputSchema`` (plus optional ``createdBy``).

    Accepts UI field names ``owner`` / ``repo`` / ``authRef`` or legacy BFF aliases
    ``ownerOrOrg`` / ``repoName`` / ``authReference`` / ``credentialReference``.
    """

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    provider: str = Field(..., max_length=32)
    owner: str = Field(
        ...,
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("owner", "ownerOrOrg"),
    )
    repo: str = Field(
        ...,
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("repo", "repoName"),
    )
    display_name: str | None = Field(default=None, alias="displayName", max_length=256)
    clone_url: str | None = Field(default=None, alias="cloneUrl", max_length=1024)
    default_branch: str = Field(default="main", alias="defaultBranch", min_length=1, max_length=256)
    credential_reference: str = Field(
        ...,
        min_length=1,
        max_length=256,
        validation_alias=AliasChoices("authRef", "authReference", "credentialReference"),
    )
    created_by: str = Field(default="qswarm-web", alias="createdBy", min_length=1, max_length=256)

    def to_legacy(self) -> RepositoryConnectionCreateRequest:
        display = (self.display_name or "").strip() or f"{self.owner}/{self.repo}"
        clone = (self.clone_url or "").strip() or None
        prov = self.provider.strip().lower()
        if prov == "other":
            # DB enum has no generic "other"; GitHub is the implemented adapter today.
            prov = "github"
        return RepositoryConnectionCreateRequest(
            provider=prov,
            display_name=display,
            owner_or_org=self.owner,
            repo_name=self.repo,
            created_by=(self.created_by or "").strip() or "qswarm-web",
            project_or_workspace=None,
            clone_url=clone,
            default_branch=self.default_branch,
            auth_type="github_pat_env",
            credential_reference=self.credential_reference,
            is_active=True,
        )


class UiRepositoryConnectionPatch(BaseModel):
    """PATCH body: Qswarm-UI ``repoConnectionInputSchema`` fields (partial updates mapped to legacy patch)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    owner: str | None = Field(default=None, validation_alias=AliasChoices("owner", "ownerOrOrg"), max_length=256)
    repo: str | None = Field(default=None, validation_alias=AliasChoices("repo", "repoName"), max_length=256)
    display_name: str | None = Field(default=None, alias="displayName", max_length=256)
    clone_url: str | None = Field(default=None, alias="cloneUrl")
    default_branch: str | None = Field(default=None, alias="defaultBranch", max_length=256)
    credential_reference: str | None = Field(
        default=None,
        validation_alias=AliasChoices("authRef", "authReference", "credentialReference"),
        max_length=256,
    )

    def to_legacy(self) -> RepositoryConnectionPatchRequest:
        data: dict[str, Any] = {}
        if self.owner is not None:
            data["owner_or_org"] = self.owner
        if self.repo is not None:
            data["repo_name"] = self.repo
        if self.display_name is not None:
            data["display_name"] = self.display_name.strip()[:256] if self.display_name.strip() else None
        if self.clone_url is not None:
            c = self.clone_url.strip()
            data["clone_url"] = c if c else None
        if self.default_branch is not None:
            data["default_branch"] = self.default_branch.strip()[:256] if self.default_branch.strip() else None
        if self.credential_reference is not None:
            data["credential_reference"] = self.credential_reference.strip()[:256] or None
        return RepositoryConnectionPatchRequest.model_validate(data)


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
