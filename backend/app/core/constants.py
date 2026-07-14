"""Application-wide constants and enum-like string groups."""

from __future__ import annotations

from enum import StrEnum


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ArtifactType(StrEnum):
    STORY_INTAKE = "story_intake"
    TEST_DESIGN = "test_design"


class AuditEventType(StrEnum):
    JIRA_STORY_FETCHED = "jira_story_fetched"
    JIRA_PICKUP_POLL_STARTED = "jira_pickup_poll_started"
    JIRA_PICKUP_CANDIDATE_FOUND = "jira_pickup_candidate_found"
    JIRA_PICKUP_SKIPPED = "jira_pickup_skipped"
    JIRA_PICKUP_WORKFLOW_CREATED = "jira_pickup_workflow_created"
    JIRA_PICKUP_WORKFLOW_STARTED = "jira_pickup_workflow_started"
    JIRA_PICKUP_POLL_COMPLETED = "jira_pickup_poll_completed"
    WORKFLOW_STARTED = "workflow_started"
    STORY_INTAKE_CREATED = "story_intake_created"
    TEST_DESIGN_CREATED = "test_design_created"
    TEST_DESIGN_PUBLISH_STARTED = "test_design_publish_started"
    JIRA_TEST_CASE_CREATED = "jira_test_case_created"
    JIRA_TEST_CASE_LINKED = "jira_test_case_linked"
    JIRA_TEST_CASE_ASSIGNMENT_FAILED = "jira_test_case_assignment_failed"
    JIRA_TEST_CASE_LINK_FAILED = "jira_test_case_link_failed"
    JIRA_PARENT_COMMENT_ADDED = "jira_parent_comment_added"
    JIRA_PARENT_COMMENT_FAILED = "jira_parent_comment_failed"
    TEST_DESIGN_PUBLISH_COMPLETED = "test_design_publish_completed"
    TEST_DESIGN_PUBLISH_FAILED = "test_design_publish_failed"
    TEST_DESIGN_FEEDBACK_RECORDED = "test_design_feedback_recorded"
    TEST_DESIGN_REFINED = "test_design_refined"
    TEST_DESIGN_REGENERATED = "test_design_regenerated"
    JIRA_DRAFT_TEST_CASES_UPDATED = "jira_draft_test_cases_updated"
    TEST_DESIGN_REPUBLISH_FAILED = "test_design_republish_failed"
    JIRA_REVIEW_ISSUE_CREATED = "jira_review_issue_created"
    JIRA_REVIEW_COMMENT_DETECTED = "jira_review_comment_detected"
    JIRA_REVIEW_COMMENT_PROCESSED = "jira_review_comment_processed"
    JIRA_REVIEW_DELTA_COMMENT_POSTED = "jira_review_delta_comment_posted"
    JIRA_REVIEW_COMMENT_PROCESSING_FAILED = "jira_review_comment_processing_failed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_COMPLETED = "workflow_completed"
    AUTOMATION_JOB_CREATED = "automation_job_created"
    AUTOMATION_JOB_STARTED = "automation_job_started"
    AUTOMATION_FRAMEWORK_SCAN_STARTED = "automation_framework_scan_started"
    AUTOMATION_FRAMEWORK_SCAN_COMPLETED = "automation_framework_scan_completed"
    AUTOMATION_FRAMEWORK_SCAN_FAILED = "automation_framework_scan_failed"
    AUTOMATION_CASE_SPEC_BUILT = "automation_case_spec_built"
    AUTOMATION_REPO_CONTEXT_COLLECTED = "automation_repo_context_collected"
    AUTOMATION_CONTEXT_COLLECTION_FAILED = "automation_context_collection_failed"
    AUTOMATION_CHANGE_PLANNING_STARTED = "automation_change_planning_started"
    AUTOMATION_CHANGE_PLAN_CREATED = "automation_change_plan_created"
    AUTOMATION_CHANGE_PLANNING_FAILED = "automation_change_planning_failed"
    AUTOMATION_CODE_GENERATION_STARTED = "automation_code_generation_started"
    AUTOMATION_CODE_GENERATED = "automation_code_generated"
    AUTOMATION_PATCH_VALIDATION_FAILED = "automation_patch_validation_failed"
    AUTOMATION_WORKSPACE_APPLY_FAILED = "automation_workspace_apply_failed"
    AUTOMATION_EXECUTION_STARTED = "automation_execution_started"
    AUTOMATION_EXECUTION_COMPLETED = "automation_execution_completed"
    AUTOMATION_EXECUTION_FAILED = "automation_execution_failed"
    AUTOMATION_FAILURE_ANALYZED = "automation_failure_analyzed"
    AUTOMATION_REPAIR_STARTED = "automation_repair_started"
    AUTOMATION_REPAIR_APPLIED = "automation_repair_applied"
    AUTOMATION_REPAIR_SKIPPED = "automation_repair_skipped"
    AUTOMATION_REPAIR_FAILED = "automation_repair_failed"
    AUTOMATION_REEXECUTION_COMPLETED = "automation_reexecution_completed"
    AUTOMATION_REVIEW_APPROVED = "automation_review_approved"
    AUTOMATION_REVIEW_REVISION_REQUESTED = "automation_review_revision_requested"
    AUTOMATION_REVIEW_REVISION_APPLIED = "automation_review_revision_applied"
    AUTOMATION_MANUAL_EDIT_ACKNOWLEDGED = "automation_manual_edit_acknowledged"
    AUTOMATION_PR_CREATION_STARTED = "automation_pr_creation_started"
    AUTOMATION_BRANCH_CREATED = "automation_branch_created"
    AUTOMATION_BASE_REFRESH_STARTED = "automation_base_refresh_started"
    AUTOMATION_BASE_REFRESH_COMPLETED = "automation_base_refresh_completed"
    AUTOMATION_BASE_REFRESH_CONFLICT = "automation_base_refresh_conflict"
    AUTOMATION_COMMIT_CREATED = "automation_commit_created"
    AUTOMATION_PR_CREATED = "automation_pr_created"
    AUTOMATION_PR_CREATION_FAILED = "automation_pr_creation_failed"
    AUTOMATION_SESSION_CREATED = "automation_session_created"
    AUTOMATION_SESSION_START_PRE_ROUND_FAILED = "automation_session_start_pre_round_failed"
    AUTOMATION_ROUND_STARTED = "automation_round_started"
    AUTOMATION_ROUND_QUEUED = "automation_round_queued"
    AUTOMATION_REPO_BOOTSTRAP_STARTED = "automation_repo_bootstrap_started"
    AUTOMATION_REPO_BOOTSTRAP = "automation_repo_bootstrap"
    AUTOMATION_PLAN_VERSION_CREATED = "automation_plan_version_created"
    AUTOMATION_PATCH_VERSION_CREATED = "automation_patch_version_created"
    AUTOMATION_EXECUTION_ATTEMPT_RECORDED = "automation_execution_attempt_recorded"
    AUTOMATION_REVIEW_REQUEST_RECORDED = "automation_review_request_recorded"
    AUTOMATION_SESSION_APPROVED = "automation_session_approved"


class PrRecordStatus(StrEnum):
    """Persisted on ``pr_records`` rows."""

    BRANCH_READY = "branch_ready"
    BASE_REFRESHED = "base_refreshed"
    BASE_REFRESH_CONFLICT = "base_refresh_conflict"
    COMMITTED = "committed"
    PR_CREATED = "pr_created"
    FAILED = "failed"


class SourceControlProviderName(StrEnum):
    """Supported source-control providers for PR / merge-request creation."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    AZURE_DEVOPS = "azure_devops"

    @classmethod
    def parse(cls, raw: str | None) -> SourceControlProviderName:
        key = (raw or "").strip().lower()
        for m in cls:
            if m.value == key:
                return m
        raise ValueError(f"unsupported_source_control_provider:{key or 'empty'}")


class CodeReviewRequestStatus(StrEnum):
    """Normalized PR / MR record lifecycle (session-scoped)."""

    PENDING_CREATION = "pending_creation"
    CREATED = "created"
    FAILED = "failed"
    CLOSED = "closed"
    MERGED = "merged"


class AutomationJobReviewActionType(StrEnum):
    APPROVE = "approve"
    REQUEST_REVISION = "request_revision"
    MANUAL_EDIT_ACK = "manual_edit_ack"


class AutomationSessionStatus(StrEnum):
    """Control-plane session status (maps from underlying AutomationJob status)."""

    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    EXECUTING = "executing"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED_FOR_PR = "approved_for_pr"
    CREATING_PR = "creating_pr"
    PR_CREATED = "pr_created"
    PR_FAILED = "pr_failed"
    FAILED = "failed"


class AutomationRevisionRoundStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class AutomationRevisionRoundTrigger(StrEnum):
    INITIAL = "initial"
    REVIEW_REVISION = "review_revision"
    MANUAL_EDIT_RERUN = "manual_edit_rerun"
    REPAIR = "repair"


class AutomationReviewRequestAction(StrEnum):
    REQUEST_REVISION = "request_revision"
    MANUAL_EDIT_ACK = "manual_edit_ack"
    APPROVE = "approve"
    REJECT = "reject"
    RERUN = "rerun"


class AutomationReviewRequestStatus(StrEnum):
    RECORDED = "recorded"
    APPLIED = "applied"
    FAILED = "failed"


class AutomationJobStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    SCANNING_FRAMEWORK = "scanning_framework"
    COLLECTING_CONTEXT = "collecting_context"
    PLANNING_CHANGES = "planning_changes"
    GENERATING_CODE = "generating_code"
    APPLYING_CHANGES = "applying_changes"
    EXECUTING = "executing"
    AWAITING_AUTOMATION_REVIEW = "awaiting_automation_review"
    REVISING_AFTER_REVIEW = "revising_after_review"
    REPAIRING = "repairing"
    AWAITING_HUMAN_INPUT = "awaiting_human_input"
    AWAITING_AUTOMATION_APPROVAL = "awaiting_automation_approval"
    APPROVED_FOR_PR = "approved_for_pr"
    CREATING_PR = "creating_pr"
    PR_CREATED = "pr_created"
    PR_CREATION_FAILED = "pr_creation_failed"
    FAILED = "failed"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"
