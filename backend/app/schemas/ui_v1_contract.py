"""Authoritative Pydantic response models for /api/v1 UI contract validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.ui_v1_stories import UiStoryListResponse, UiStorySummary

StoryReadiness = Literal["ready", "partial", "missing_ac"]
AcceptanceCriteriaStatus = Literal["ready", "partial", "missing_ac"]
Sprint1Stage = Literal[
    "discovered",
    "intake_ready",
    "analyzing_requirements",
    "analysis_ready",
    "preparing_test_design_plan",
    "awaiting_plan_approval",
    "plan_revision_requested",
    "plan_approved",
    "generating_test_cases",
    "awaiting_test_case_review",
    "revising_test_cases",
    "approved",
    "publishing",
    "published",
    "automation_ready",
    "completed",
    "legacy_awaiting_approval",
    "failed",
]
Sprint1NextAction = Literal[
    "analyze_requirements",
    "prepare_plan",
    "approve_plan",
    "request_plan_revision",
    "generate_test_cases",
    "request_revision",
    "approve_test_design",
    "publish_test_cases",
    "view_automation_backlog",
]
WorkflowRunBackendStatus = Literal[
    "pending",
    "running",
    "awaiting_approval",
    "approved",
    "rejected",
    "completed",
    "failed",
]

_MODEL_CFG = ConfigDict(populate_by_name=True, serialize_by_alias=True, extra="forbid")


class UiApiErrorDetail(BaseModel):
    """Actual FastAPI HTTP error body (``detail`` key, not ``error``)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str | None = None


class UiApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: UiApiErrorDetail


class UiStoryDetail(BaseModel):
    model_config = _MODEL_CFG

    story_key: str = Field(alias="storyKey")
    title: str | None = None
    description: str | None = None
    labels: list[str] = Field(default_factory=list)
    status: str | None = None
    issue_type: str | None = Field(default=None, alias="issueType")
    priority: str | None = None
    active_workflow_run_id: str | None = Field(default=None, alias="activeWorkflowRunId")
    active_workflow_run_status: str | None = Field(default=None, alias="activeWorkflowRunStatus")
    active_workflow_stage: str | None = Field(default=None, alias="activeWorkflowStage")


class UiSourceStoryRef(BaseModel):
    model_config = _MODEL_CFG

    story_key: str = Field(alias="storyKey")
    intake_artifact_id: str | None = Field(default=None, alias="intakeArtifactId")


class UiProductWorkspace(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True, extra="allow")

    mode: str | None = None
    stage: str | None = None


class UiArtifactVersionRef(BaseModel):
    model_config = _MODEL_CFG

    version: int
    artifact_id: str = Field(alias="artifactId")
    content: dict[str, Any]
    created_at: str | None = Field(default=None, alias="createdAt")
    plan_approved: bool | None = Field(default=None, alias="planApproved")
    plan_approved_at: str | None = Field(default=None, alias="planApprovedAt")
    plan_approved_by: str | None = Field(default=None, alias="planApprovedBy")


class UiTestDesignVersion(BaseModel):
    model_config = _MODEL_CFG

    id: str
    artifact_id: str = Field(alias="artifactId")
    version_number: int = Field(alias="versionNumber")
    parent_version_id: str | None = Field(default=None, alias="parentVersionId")
    version_action: str | None = Field(default=None, alias="versionAction")
    source_feedback_id: str | None = Field(default=None, alias="sourceFeedbackId")
    is_current: bool = Field(alias="isCurrent")
    created_by: str | None = Field(default=None, alias="createdBy")
    created_at: str = Field(alias="createdAt")
    notes: str | None = None


class UiReviewIssueRef(BaseModel):
    model_config = _MODEL_CFG

    review_jira_issue_key: str | None = Field(default=None, alias="reviewJiraIssueKey")
    publish_status: str | None = Field(default=None, alias="publishStatus")


class UiTestCaseRecord(BaseModel):
    model_config = _MODEL_CFG

    id: str
    registry_key: str = Field(alias="registryKey")
    workflow_run_id: str = Field(alias="workflowRunId")
    source_story_key: str = Field(alias="sourceStoryKey")
    source_system: str | None = Field(default=None, alias="sourceSystem")
    external_id: str | None = Field(default=None, alias="externalId")
    external_url: str | None = Field(default=None, alias="externalUrl")
    title: str | None = None
    summary: str | None = None
    objective: str | None = None
    case_type: str | None = Field(default=None, alias="caseType")
    case_index: int | None = Field(default=None, alias="caseIndex")
    steps: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list, alias="expectedResults")
    preconditions: list[str] = Field(default_factory=list)
    approval_status: str | None = Field(default=None, alias="approvalStatus")
    publication_status: str | None = Field(default=None, alias="publicationStatus")
    publication_error: str | None = Field(default=None, alias="publicationError")
    published_at: str | None = Field(default=None, alias="publishedAt")
    automation_status: str | None = Field(default=None, alias="automationStatus")
    automation_session_id: str | None = Field(default=None, alias="automationSessionId")
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")


class UiTestCaseListResponse(BaseModel):
    model_config = _MODEL_CFG

    items: list[UiTestCaseRecord]


class UiTestDesignRunDetail(BaseModel):
    """GET /api/v1/test-design-runs/{runId} — authoritative aggregate."""

    model_config = _MODEL_CFG

    id: str
    story_key: str = Field(alias="storyKey")
    workflow_name: str = Field(alias="workflowName")
    status: str
    current_step: str = Field(alias="currentStep")
    current_stage: str = Field(alias="currentStage")
    next_actions: list[str] = Field(alias="nextActions")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    initiated_by: str = Field(alias="initiatedBy")
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    source_story: UiSourceStoryRef = Field(alias="sourceStory")
    requirement_analysis: UiArtifactVersionRef | None = Field(default=None, alias="requirementAnalysis")
    test_design_plan: UiArtifactVersionRef | None = Field(default=None, alias="testDesignPlan")
    versions: list[UiTestDesignVersion] = Field(default_factory=list)
    review_issue: UiReviewIssueRef | None = Field(default=None, alias="reviewIssue")
    test_case_records: list[UiTestCaseRecord] = Field(default_factory=list, alias="testCaseRecords")
    automation_ready_test_cases: list[UiTestCaseRecord] = Field(
        default_factory=list, alias="automationReadyTestCases"
    )
    approval_id: str | None = Field(default=None, alias="approvalId")
    product_workspace: UiProductWorkspace = Field(default_factory=UiProductWorkspace, alias="productWorkspace")


class UiReviewSummary(BaseModel):
    model_config = _MODEL_CFG

    status: str
    current_version: int = Field(alias="currentVersion")
    test_case_count: int = Field(alias="testCaseCount")
    gaps_count: int = Field(alias="gapsCount")
    automation_candidate_count: int = Field(alias="automationCandidateCount")
    next_actions: list[str] = Field(alias="nextActions")
    workflow_status: str = Field(alias="workflowStatus")


class UiDraftTestCase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True, extra="allow")

    registry_key: str = Field(alias="registryKey")
    draft_key: str = Field(alias="draftKey")
    title: str
    objective: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list, alias="expectedResults")
    test_type: str | None = Field(default=None, alias="testType")
    priority: str | None = None
    automation_suitability: str | None = Field(default=None, alias="automationSuitability")
    source_story_key: str | None = Field(default=None, alias="sourceStoryKey")
    generated_version: int | None = Field(default=None, alias="generatedVersion")
    status: str | None = None
    external_id: str | None = Field(default=None, alias="externalId")


class UiConversationEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True, extra="allow")

    id: str
    type: str
    actor: str | None = None
    text: str | None = None
    scope: str | None = None
    created_at: str | None = Field(default=None, alias="createdAt")
    status: str | None = None


class UiTestDesignReviewData(BaseModel):
    model_config = _MODEL_CFG

    workflow_run_id: str = Field(alias="workflowRunId")
    review_summary: UiReviewSummary = Field(alias="reviewSummary")
    test_cases: list[UiDraftTestCase] = Field(default_factory=list, alias="testCases")
    conversation: list[UiConversationEvent] = Field(default_factory=list)
    versions: list[UiTestDesignVersion] = Field(default_factory=list)
    publication: dict[str, Any] = Field(default_factory=dict)


class UiRevisionResult(BaseModel):
    model_config = _MODEL_CFG

    ok: bool
    new_version_number: int = Field(alias="newVersionNumber")
    feedback_id: str = Field(alias="feedbackId")
    action: str


# Re-export story list models for contract tests
__all__ = [
    "UiStoryListResponse",
    "UiStorySummary",
    "UiStoryDetail",
    "UiTestDesignRunDetail",
    "UiTestDesignReviewData",
    "UiTestCaseListResponse",
    "UiTestCaseRecord",
    "UiApiErrorResponse",
    "UiRevisionResult",
]
