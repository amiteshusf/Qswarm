"""Automation job lifecycle (Sprint 2 — internal execution entity)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    ActorType,
    AuditEventType,
    AutomationJobReviewActionType,
    AutomationJobStatus,
    PrRecordStatus,
)
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_job_review_action import AutomationJobReviewAction
from app.db.models.pr_record import PrRecord
from app.db.models.workflow_run import WorkflowRun
from app.schemas.automation import AutomationJobCreateRequest
from app.services import audit_service
from app.services.case_enhancement_service import build_case_spec_from_job
from app.services.framework_scan_service import (
    FrameworkScanError,
    resolve_repo_path,
    scan_local_repo,
)
from app.services.change_planning_service import (
    PlanningValidationError,
    create_validated_change_plan,
)
from app.services.code_generation_service import run_code_generation_and_apply
from app.services.patch_validation_service import PatchValidationError
from app.services.repo_context_service import RepoContextError, collect_repo_context
from app.services.execution_service import (
    execution_prerequisites_met,
    resolve_target_test_file,
    run_playwright_execution_for_job,
)
from app.services.automation_review_service import (
    apply_review_revision_and_execute,
    rerun_execution_after_manual_ack,
)
from app.services.pr_creation_service import create_pull_request_for_job
from app.services.repair_service import run_repair
from app.services.workspace_service import WorkspaceApplyError


class ChangePlanRejected(Exception):
    """Raised when the provider plan fails validation after the job is marked failed."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class PatchRejected(Exception):
    """Raised when generated patch fails validation after the job is marked failed."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class WorkspaceApplyRejected(Exception):
    """Raised when validated patch cannot be applied after the job is marked failed."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _framework_type_from_job(job: AutomationJob) -> str | None:
    blob = job.framework_summary_json
    if isinstance(blob, dict):
        ft = blob.get("framework_type")
        return str(ft) if ft is not None else None
    return None


def _case_input_from_create(body: AutomationJobCreateRequest) -> dict[str, Any] | None:
    """Serialize optional case fields into one JSON blob on the job."""
    d: dict[str, Any] = {}
    if body.case_title is not None and str(body.case_title).strip():
        d["case_title"] = str(body.case_title).strip()
    if body.case_description is not None and str(body.case_description).strip():
        d["case_description"] = str(body.case_description).strip()
    if body.preconditions:
        pl = [x.strip() for x in body.preconditions if isinstance(x, str) and x.strip()]
        if pl:
            d["preconditions"] = pl
    if body.steps:
        sl = [x.strip() for x in body.steps if isinstance(x, str) and x.strip()]
        if sl:
            d["steps"] = sl
    if body.expected_results:
        el = [x.strip() for x in body.expected_results if isinstance(x, str) and x.strip()]
        if el:
            d["expected_results"] = el
    return d if d else None


def job_to_response(job: AutomationJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "approved_case_id": job.approved_case_id,
        "workflow_run_id": job.workflow_run_id,
        "repo_id": job.repo_id,
        "repo_owner": job.repo_owner,
        "repo_name": job.repo_name,
        "repo_path": job.repo_path,
        "base_branch": job.base_branch,
        "branch_name": job.branch_name,
        "requested_by": job.requested_by,
        "status": job.status,
        "blocked_reason": job.blocked_reason,
        "latest_attempt_number": job.latest_attempt_number,
        "framework_type": _framework_type_from_job(job),
        "framework_summary_json": job.framework_summary_json,
        "case_spec_json": job.case_spec_json,
        "repo_context_json": job.repo_context_json,
        "change_plan_json": job.change_plan_json,
        "generated_patch_json": job.generated_patch_json,
        "execution_result_json": job.execution_result_json,
        "failure_analysis_json": job.failure_analysis_json,
        "repair_result_json": job.repair_result_json,
        "final_result_json": job.final_result_json,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def get_automation_job(db: Session, job_id: uuid.UUID) -> AutomationJob | None:
    return db.get(AutomationJob, job_id)


def list_automation_jobs(db: Session, *, limit: int = 100) -> list[AutomationJob]:
    limit = max(1, min(limit, 500))
    return list(
        db.execute(
            select(AutomationJob).order_by(AutomationJob.created_at.desc()).limit(limit)
        ).scalars().all()
    )


def create_automation_job(db: Session, body: AutomationJobCreateRequest) -> AutomationJob:
    if body.workflow_run_id is not None:
        if db.get(WorkflowRun, body.workflow_run_id) is None:
            raise ValueError("workflow_run_not_found")

    job = AutomationJob(
        approved_case_id=body.approved_case_id.strip(),
        workflow_run_id=body.workflow_run_id,
        repo_id=body.repo_id.strip() if body.repo_id else None,
        repo_owner=body.repo_owner.strip() if body.repo_owner else None,
        repo_name=body.repo_name.strip() if body.repo_name else None,
        repo_path=body.repo_path.strip() if body.repo_path else None,
        base_branch=(body.base_branch.strip() or "main"),
        requested_by=body.requested_by.strip(),
        status=AutomationJobStatus.PENDING.value,
        case_input_json=_case_input_from_create(body),
    )
    db.add(job)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_JOB_CREATED.value,
        actor_type=ActorType.USER.value,
        actor_id=body.requested_by.strip(),
        workflow_run_id=body.workflow_run_id,
        step_name="automation_job",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"approved_case_id": job.approved_case_id},
    )
    return job


def start_automation_job(db: Session, job_id: uuid.UUID, *, actor_id: str) -> AutomationJob:
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.PENDING.value:
        raise ValueError("job_not_startable")

    path = resolve_repo_path(job.repo_path)

    job.status = AutomationJobStatus.SCANNING_FRAMEWORK.value
    job.blocked_reason = None
    db.flush()

    aid = actor_id.strip() or job.requested_by
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_JOB_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="automation_job_start",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"status": job.status, "repo_path": job.repo_path},
    )
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_FRAMEWORK_SCAN_STARTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="framework_scan",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"repo_path": str(path)},
    )

    try:
        summary = scan_local_repo(path)
    except FrameworkScanError as e:
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = e.message
        job.framework_summary_json = None
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_FRAMEWORK_SCAN_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="framework_scan",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"code": e.code, "message": e.message},
        )
        db.flush()
        return job

    job.framework_summary_json = summary
    fw_type = summary.get("framework_type")

    if fw_type == "unknown":
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = "Unsupported or unknown framework"
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_FRAMEWORK_SCAN_COMPLETED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="framework_scan",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"framework_type": "unknown", "outcome": "unsupported"},
        )
        db.flush()
        return job

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_FRAMEWORK_SCAN_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="framework_scan",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"framework_type": fw_type, "outcome": "ok"},
    )

    job.status = AutomationJobStatus.COLLECTING_CONTEXT.value
    db.flush()

    case_spec = build_case_spec_from_job(job)
    job.case_spec_json = case_spec
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_CASE_SPEC_BUILT.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="case_enhancement",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"title": case_spec.get("title")},
    )

    try:
        repo_ctx = collect_repo_context(path, summary, case_spec)
    except RepoContextError as e:
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = e.message
        job.repo_context_json = None
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_CONTEXT_COLLECTION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="repo_context",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": e.message},
        )
        db.flush()
        return job

    job.repo_context_json = repo_ctx
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REPO_CONTEXT_COLLECTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="repo_context",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"similar_tests": len(repo_ctx.get("similar_test_files") or [])},
    )

    job.status = AutomationJobStatus.PLANNING_CHANGES.value
    job.blocked_reason = None
    db.flush()
    return job


def _plan_prerequisites_met(job: AutomationJob) -> bool:
    for blob in (job.framework_summary_json, job.case_spec_json, job.repo_context_json):
        if not isinstance(blob, dict):
            return False
    return True


def _generation_prerequisites_met(job: AutomationJob) -> bool:
    if not _plan_prerequisites_met(job):
        return False
    if not isinstance(job.change_plan_json, dict):
        return False
    if job.repo_path is None or not str(job.repo_path).strip():
        return False
    return True


def plan_automation_job_changes(
    db: Session, job_id: uuid.UUID, *, actor_id: str
) -> AutomationJob:
    """
    Run change planning from ``planning_changes`` only.

    On success: ``change_plan_json`` set, status ``generating_code``.
    On validation failure: status ``failed``, ``blocked_reason`` set, no plan persisted;
    raises :class:`ChangePlanRejected` for the API layer.
    """
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.PLANNING_CHANGES.value:
        raise ValueError("job_not_plan_ready")
    if not _plan_prerequisites_met(job):
        raise ValueError("plan_prerequisites_missing")

    aid = actor_id.strip() or job.requested_by
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_CHANGE_PLANNING_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="change_planning",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={},
    )
    db.flush()

    try:
        plan = create_validated_change_plan(job)
    except PlanningValidationError as e:
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = (e.message or "change_plan_validation_failed")[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_CHANGE_PLANNING_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="change_planning",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": job.blocked_reason},
        )
        db.flush()
        raise ChangePlanRejected(job.blocked_reason or str(e)) from e

    job.change_plan_json = plan
    job.status = AutomationJobStatus.GENERATING_CODE.value
    job.blocked_reason = None
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_CHANGE_PLAN_CREATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="change_planning",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"target_test_file": plan.get("target_test_file")},
    )
    db.flush()
    return job


def describe_plan_outcome(job: AutomationJob) -> str:
    """Short message for ``POST .../plan`` response body."""
    if job.status == AutomationJobStatus.GENERATING_CODE.value:
        return "Change plan created successfully"
    if job.status == AutomationJobStatus.FAILED.value:
        return job.blocked_reason or "Change planning failed."
    return "Automation job updated."


def generate_code_for_automation_job(
    db: Session, job_id: uuid.UUID, *, actor_id: str
) -> AutomationJob:
    """
    Generate patch from ``change_plan_json``, validate, apply to ``repo_path``.

    Valid only from ``generating_code``. On success: ``generated_patch_json``,
    status ``executing``. On failure: ``failed`` + ``blocked_reason``;
    raises ``PatchRejected`` or ``WorkspaceApplyRejected``.
    """
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.GENERATING_CODE.value:
        raise ValueError("job_not_generate_ready")
    if not _generation_prerequisites_met(job):
        raise ValueError("generation_prerequisites_missing")

    aid = actor_id.strip() or job.requested_by
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_CODE_GENERATION_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="code_generation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={},
    )
    db.flush()

    try:
        summary = run_code_generation_and_apply(job)
    except PatchValidationError as e:
        msg = (e.message or "patch_validation_failed")[:2048]
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = msg
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PATCH_VALIDATION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="code_generation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": msg},
        )
        db.flush()
        raise PatchRejected(msg) from e
    except (WorkspaceApplyError, FrameworkScanError) as e:
        msg = getattr(e, "message", str(e))[:2048]
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = msg
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_WORKSPACE_APPLY_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="code_generation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": msg},
        )
        db.flush()
        raise WorkspaceApplyRejected(msg) from e

    job.generated_patch_json = summary
    job.status = AutomationJobStatus.EXECUTING.value
    job.blocked_reason = None
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_CODE_GENERATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="code_generation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "files": len(summary.get("generated_files") or []),
            "provider": summary.get("provider"),
        },
    )
    db.flush()
    return job


def describe_generate_outcome(job: AutomationJob) -> str:
    """Short message for ``POST .../generate`` response body."""
    if job.status == AutomationJobStatus.EXECUTING.value:
        return "Code generated and applied successfully"
    if job.status == AutomationJobStatus.FAILED.value:
        return job.blocked_reason or "Code generation or apply failed."
    return "Automation job updated."


def execute_automation_job(db: Session, job_id: uuid.UUID, *, actor_id: str) -> AutomationJob:
    """
    Run Playwright for the resolved target test file under ``repo_path``.

    Valid only from ``executing``. Persists bounded ``execution_result_json``.
    On test/process success -> ``awaiting_automation_review``; else ``failed``.
    """
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.EXECUTING.value:
        raise ValueError("job_not_executable")
    if not execution_prerequisites_met(job):
        raise ValueError("execution_prerequisites_missing")

    aid = actor_id.strip() or job.requested_by
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_EXECUTION_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="playwright_execution",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"target_test_file": resolve_target_test_file(job)},
    )
    db.flush()

    try:
        result = run_playwright_execution_for_job(job)
    except Exception as e:  # pragma: no cover — defensive
        job.execution_result_json = {
            "framework_type": "playwright",
            "command": [],
            "target_test_file": resolve_target_test_file(job) or "",
            "success": False,
            "exit_code": None,
            "duration_ms": 0,
            "stdout_tail": "",
            "stderr_tail": "",
            "artifact_paths": [],
            "notes": [str(e)][:20],
        }
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = str(e)[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_EXECUTION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="playwright_execution",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"error": str(e)[:500]},
        )
        db.flush()
        return job

    job.execution_result_json = result
    if result.get("success"):
        job.status = AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
        job.blocked_reason = None
    else:
        job.status = AutomationJobStatus.FAILED.value
        notes = result.get("notes") or []
        br = "Playwright execution failed"
        if notes:
            br = str(notes[0])
        job.blocked_reason = br[:2048]

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_EXECUTION_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="playwright_execution",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "success": bool(result.get("success")),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "target_test_file": result.get("target_test_file"),
        },
    )
    db.flush()
    return job


def describe_execute_outcome(job: AutomationJob) -> str:
    """Short message for ``POST .../execute`` response body."""
    ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else None
    if job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        return "Execution completed successfully"
    if job.status == AutomationJobStatus.FAILED.value and ex:
        notes = ex.get("notes") or []
        joined = " ".join(str(n) for n in notes).lower()
        if "timed out" in joined:
            return "Execution failed: timed out"
        if ex.get("launch_error"):
            return f"Execution failed: {str(ex.get('launch_error'))[:200]}"
        if ex.get("exit_code") not in (None, 0):
            return "Execution completed with test failures"
        return job.blocked_reason or "Execution failed."
    if job.status == AutomationJobStatus.FAILED.value:
        return job.blocked_reason or "Execution failed."
    return "Automation job updated."


def repair_automation_job(db: Session, job_id: uuid.UUID, *, actor_id: str) -> AutomationJob:
    """
    Analyze failed execution, optionally apply one repair patch, re-run Playwright once.

    Valid only for ``failed`` jobs with ``execution_result_json``. At most once per job.
    """
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.repair_result_json is not None:
        raise ValueError("repair_already_attempted")
    if job.status != AutomationJobStatus.FAILED.value:
        raise ValueError("job_not_repairable_state")
    run_repair(db, job, actor_id=actor_id)
    return job


def describe_repair_outcome(job: AutomationJob) -> str:
    """Short message for ``POST .../repair`` response body."""
    rr = job.repair_result_json if isinstance(job.repair_result_json, dict) else {}
    if job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        return "Repair succeeded and execution passed"
    if job.status == AutomationJobStatus.AWAITING_HUMAN_INPUT.value:
        return "Repair not attempted; human input required"
    if job.status == AutomationJobStatus.FAILED.value:
        if rr.get("attempted") and rr.get("reexecution_success") is False:
            return "Repair attempted but execution still failed"
        if rr.get("skipped_reason") == "not_repairable":
            return "Failure is not repairable automatically"
        if rr.get("skipped_reason") == "human_input":
            return "Repair not attempted; human input required"
        if rr.get("skipped_reason") == "provider_skipped":
            return "Repair was not applied by the provider"
    return "Repair flow finished."


def describe_start_outcome(job: AutomationJob) -> str:
    """Short message for ``POST .../start`` response body."""
    if job.status == AutomationJobStatus.PLANNING_CHANGES.value:
        return "Framework scan, case spec, and repo context are ready; job is in planning_changes."
    if job.status == AutomationJobStatus.COLLECTING_CONTEXT.value:
        return "Job is collecting context (partial state — retry or check logs)."
    if job.status == AutomationJobStatus.FAILED.value:
        return job.blocked_reason or "Job failed during start pipeline."
    return "Automation job updated."


def approve_automation_job_for_pr(db: Session, job_id: uuid.UUID, *, actor_id: str) -> AutomationJob:
    """Human approval for PR readiness; valid only from ``awaiting_automation_review``."""
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        raise ValueError("review_wrong_state")
    aid = actor_id.strip()
    if not aid:
        raise ValueError("review_actor_missing")

    db.add(
        AutomationJobReviewAction(
            automation_job_id=job.id,
            action_type=AutomationJobReviewActionType.APPROVE.value,
            actor_id=aid,
            instruction_text=None,
            metadata_json=None,
        )
    )
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_APPROVED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="automation_review",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={},
    )
    job.status = AutomationJobStatus.APPROVED_FOR_PR.value
    job.blocked_reason = None
    db.flush()
    return job


def describe_approve_outcome(job: AutomationJob) -> str:
    if job.status == AutomationJobStatus.APPROVED_FOR_PR.value:
        return "Automation approved for PR creation"
    return "Automation job updated."


def request_automation_job_revision(
    db: Session, job_id: uuid.UUID, *, actor_id: str, instruction_text: str
) -> AutomationJob:
    """
    Record revision request, move to ``revising_after_review``, apply stub/LLM patch, re-execute.
    """
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        raise ValueError("review_wrong_state")
    inst = str(instruction_text or "").strip()
    if not inst:
        raise ValueError("revision_instruction_missing")
    aid = actor_id.strip()
    if not aid:
        raise ValueError("review_actor_missing")
    if not execution_prerequisites_met(job):
        raise ValueError("review_prerequisites_missing")

    db.add(
        AutomationJobReviewAction(
            automation_job_id=job.id,
            action_type=AutomationJobReviewActionType.REQUEST_REVISION.value,
            actor_id=aid,
            instruction_text=inst[:20000],
            metadata_json=None,
        )
    )
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_REVISION_REQUESTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="automation_review",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"instruction_preview": inst[:200]},
    )
    job.status = AutomationJobStatus.REVISING_AFTER_REVIEW.value
    job.blocked_reason = None
    db.flush()

    apply_review_revision_and_execute(db, job, instruction_text=inst, actor_id=aid)
    return job


def describe_revision_outcome(job: AutomationJob) -> str:
    if job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        return "Revision applied and execution passed"
    if job.status == AutomationJobStatus.AWAITING_HUMAN_INPUT.value:
        return "Revision applied but human input is required"
    if job.status == AutomationJobStatus.FAILED.value:
        br = (job.blocked_reason or "").lower()
        if any(
            x in br
            for x in (
                "revision was not produced",
                "not produced",
                "empty reviewer",
                "no target_test_file",
                "not in plan create",
            )
        ):
            return "Revision was not produced by the provider"
        return "Revision applied but execution still failed"
    if job.status == AutomationJobStatus.REVISING_AFTER_REVIEW.value:
        return "Revision flow in progress"
    return "Revision flow finished."


def acknowledge_manual_edit_and_rerun(
    db: Session, job_id: uuid.UUID, *, actor_id: str, note: str
) -> AutomationJob:
    """Acknowledge out-of-band manual edits and re-run Playwright (no provider patch)."""
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status not in (
        AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value,
        AutomationJobStatus.FAILED.value,
    ):
        raise ValueError("review_wrong_state")
    n = str(note or "").strip()
    if not n:
        raise ValueError("manual_ack_note_missing")
    aid = actor_id.strip()
    if not aid:
        raise ValueError("review_actor_missing")
    if not execution_prerequisites_met(job):
        raise ValueError("review_prerequisites_missing")

    db.add(
        AutomationJobReviewAction(
            automation_job_id=job.id,
            action_type=AutomationJobReviewActionType.MANUAL_EDIT_ACK.value,
            actor_id=aid,
            instruction_text=None,
            metadata_json={"note": n[:5000]},
        )
    )
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_MANUAL_EDIT_ACKNOWLEDGED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="automation_review",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"note_preview": n[:200]},
    )
    job.status = AutomationJobStatus.REVISING_AFTER_REVIEW.value
    job.blocked_reason = None
    db.flush()

    rerun_execution_after_manual_ack(db, job, actor_id=aid)
    return job


def describe_manual_ack_outcome(job: AutomationJob) -> str:
    if job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        return "Manual edits acknowledged and execution passed"
    if job.status == AutomationJobStatus.AWAITING_HUMAN_INPUT.value:
        return "Execution after manual edits requires human input"
    if job.status == AutomationJobStatus.FAILED.value:
        return "Manual edits acknowledged but execution still failed"
    if job.status == AutomationJobStatus.REVISING_AFTER_REVIEW.value:
        return "Manual edit acknowledgement in progress"
    return "Manual edit acknowledgement finished."


def create_pr_for_automation_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    actor_id: str,
    repo_owner: str | None = None,
    repo_name: str | None = None,
) -> tuple[AutomationJob, PrRecord]:
    """
    Pre-PR base refresh, optional Playwright re-run, commit, push, GitHub PR.

    Returns ``(job, pr_record)``. Raises ``ValueError`` for ``job_not_found``,
    ``pr_wrong_state``, ``pr_prerequisites_missing``.
    """
    job = db.get(AutomationJob, job_id)
    if job is None:
        raise ValueError("job_not_found")
    pr_row: PrRecord = create_pull_request_for_job(
        db,
        job,
        actor_id=actor_id,
        repo_owner=repo_owner,
        repo_name=repo_name,
    )
    return job, pr_row


def describe_create_pr_outcome(job: AutomationJob, pr_row: PrRecord | None = None) -> str:
    """Human-readable outcome for ``POST .../create-pr``."""
    if job.status == AutomationJobStatus.PR_CREATED.value:
        return "Pull request created successfully"
    if job.status == AutomationJobStatus.AWAITING_HUMAN_INPUT.value:
        st = getattr(pr_row, "status", None) if pr_row is not None else None
        if st == PrRecordStatus.BASE_REFRESH_CONFLICT.value:
            return "PR creation blocked: branch refresh conflict"
        return "PR creation blocked: human input required before PR"
    if job.status == AutomationJobStatus.FAILED.value:
        br = (job.blocked_reason or "").lower()
        if "github" in br or "api error" in br:
            return "PR creation failed: GitHub error"
        if "nothing to commit" in br:
            return "PR creation failed: nothing to commit"
        if "execution" in br or "playwright" in br or "exit_code" in br:
            return "PR creation failed: execution failed after base refresh"
        return "PR creation failed: git or validation error"
    return "PR creation finished."
