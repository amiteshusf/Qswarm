"""Request bodies for /api/v1 (camelCase aliases; accepts snake_case too)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.schemas.automation_session import (
    AutomationSessionCreateRequest,
    AutomationSessionPlanRevisionBody,
    AutomationSessionStartRequest,
)
from app.schemas.repository_connection import (
    BranchPolicyCreateRequest,
    BranchPolicyPatchRequest,
    RepositoryConnectionCreateRequest,
    RepositoryConnectionPatchRequest,
)


class UiAutomationSessionCreate(BaseModel):
    """
    Create body: Qswarm-UI ``sessionCreateInputSchema`` when ``repositoryConnectionId`` is set;
    otherwise legacy QSwarm Web fields (``approvedCaseId``, ``repoPath``, …).
    """

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    repository_connection_id: uuid.UUID | None = Field(default=None, alias="repositoryConnectionId")
    branch_policy_id: uuid.UUID | None = Field(default=None, alias="branchPolicyId")
    coding_engine: str = Field(
        default="stub",
        max_length=64,
        validation_alias=AliasChoices("engine", "codingEngine"),
    )
    source_reference: str | None = Field(
        default=None,
        max_length=512,
        validation_alias=AliasChoices("sourceRef", "sourceReference"),
    )
    source_label: str | None = Field(default=None, alias="sourceLabel", max_length=512)
    approved_case_id: str | None = Field(default=None, alias="approvedCaseId", max_length=512)
    created_by: str = Field(default="qswarm-web", alias="createdBy", min_length=1, max_length=256)
    source_system: str | None = Field(default=None, alias="sourceSystem", max_length=64)
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

    @model_validator(mode="after")
    def _sync_case_and_source_ref(self) -> UiAutomationSessionCreate:
        ap = (self.approved_case_id or "").strip()
        ref = (self.source_reference or "").strip()
        if self.repository_connection_id is not None:
            if not ref and ap:
                object.__setattr__(self, "source_reference", ap)
                ref = ap
            if not ap and ref:
                object.__setattr__(self, "approved_case_id", ref)
                ap = ref
            if not ap:
                raise ValueError("source_ref_required")
        else:
            if not ap:
                raise ValueError("approved_case_id_required")
            if not ref:
                object.__setattr__(self, "source_reference", ap)
        return self

    def to_legacy(self, *, db: Any = None) -> AutomationSessionCreateRequest:
        base_branch = self.base_branch
        if db is not None and self.branch_policy_id is not None:
            from app.db.models.repository_branch_policy import RepositoryBranchPolicy

            pol = db.get(RepositoryBranchPolicy, self.branch_policy_id)
            if pol is not None and pol.base_branch_default:
                base_branch = pol.base_branch_default
        steps = self.steps
        if steps is None and self.repo_path:
            steps = ["open"]
        return AutomationSessionCreateRequest(
            approved_case_id=(self.approved_case_id or "").strip(),
            created_by=(self.created_by or "").strip() or "qswarm-web",
            coding_engine=self.coding_engine,
            source_system=self.source_system
            or ("qswarm_ui" if self.repository_connection_id is not None else None),
            source_reference=(self.source_reference or "").strip() or None,
            repo_id=self.repo_id,
            repo_owner=self.repo_owner,
            repo_name=self.repo_name,
            repo_path=self.repo_path,
            base_branch=base_branch,
            workflow_run_id=self.workflow_run_id,
            case_title=self.source_label or self.case_title,
            case_description=self.case_description,
            preconditions=self.preconditions,
            steps=steps,
            expected_results=self.expected_results,
            repository_connection_id=self.repository_connection_id,
        )


class UiAutomationSessionStart(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str | None = Field(default=None, alias="actorId", max_length=256)
    repository_connection_id: uuid.UUID | None = Field(default=None, alias="repositoryConnectionId")

    def to_legacy(self) -> AutomationSessionStartRequest:
        return AutomationSessionStartRequest.model_validate(self.model_dump(by_alias=False))


class UiAutomationSessionPlanRevision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(default="qswarm-web", alias="actorId", max_length=256)
    instruction_text: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        validation_alias=AliasChoices("instruction", "instructionText"),
    )

    def to_legacy(self) -> AutomationSessionPlanRevisionBody:
        aid = (self.actor_id or "").strip() or "qswarm-web"
        return AutomationSessionPlanRevisionBody(actor_id=aid, instruction_text=self.instruction_text)


class UiAutomationSessionApprove(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(default="qswarm-web", alias="actorId", min_length=1, max_length=256)

    def to_legacy_actor(self) -> str:
        return self.actor_id


class UiAutomationSessionRevision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(default="qswarm-web", alias="actorId", max_length=256)
    instruction_text: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        validation_alias=AliasChoices("instruction", "instructionText"),
    )
    target_scope: str | None = Field(
        default=None,
        max_length=512,
        validation_alias=AliasChoices("scope", "targetScope"),
    )

    def to_legacy_tuple(self) -> tuple[str, str, str | None]:
        aid = (self.actor_id or "").strip() or "qswarm-web"
        return aid, self.instruction_text, self.target_scope


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
    """Qswarm-UI ``branchPolicyInputSchema`` + ``repositoryConnectionId`` (required to attach policy)."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=256)
    repository_connection_id: uuid.UUID = Field(..., alias="repositoryConnectionId")
    base_branch: str = Field(..., alias="baseBranch", min_length=1, max_length=256)
    branch_pattern: str = Field(..., alias="branchPattern", min_length=1, max_length=512)
    pr_title_template: str = Field(..., alias="prTitleTemplate", min_length=1, max_length=512)
    pr_body_template: str = Field(default="", alias="prBodyTemplate")

    def to_legacy(self) -> BranchPolicyCreateRequest:
        body = (self.pr_body_template or "").strip()
        return BranchPolicyCreateRequest(
            base_branch_default=self.base_branch.strip()[:256],
            branch_naming_pattern=self.branch_pattern.strip()[:512],
            allow_session_override=True,
            commit_message_template=None,
            pr_title_template=self.pr_title_template.strip()[:512],
            pr_body_template=body if body else None,
            default_reviewers_json=None,
            default_labels_json=None,
        )


class UiBranchPolicyPatch(BaseModel):
    """Partial update using Qswarm-UI field names where provided."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=256)
    base_branch: str | None = Field(default=None, alias="baseBranch", max_length=256)
    branch_pattern: str | None = Field(default=None, alias="branchPattern", max_length=512)
    pr_title_template: str | None = Field(default=None, alias="prTitleTemplate", max_length=512)
    pr_body_template: str | None = Field(default=None, alias="prBodyTemplate")
    repository_connection_id: uuid.UUID | None = Field(default=None, alias="repositoryConnectionId")

    def to_legacy(self) -> BranchPolicyPatchRequest:
        data: dict[str, Any] = {}
        if self.base_branch is not None:
            data["base_branch_default"] = self.base_branch.strip()[:256] if self.base_branch.strip() else None
        if self.branch_pattern is not None:
            data["branch_naming_pattern"] = self.branch_pattern.strip()[:512] if self.branch_pattern.strip() else None
        if self.pr_title_template is not None:
            data["pr_title_template"] = self.pr_title_template.strip()[:512] if self.pr_title_template.strip() else None
        if self.pr_body_template is not None:
            b = self.pr_body_template.strip()
            data["pr_body_template"] = b if b else None
        return BranchPolicyPatchRequest.model_validate(data)
