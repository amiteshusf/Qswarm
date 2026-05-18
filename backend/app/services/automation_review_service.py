"""Post-review revision: provider patch, validate, apply, re-execute, finalize status."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.constants import ActorType, AuditEventType, AutomationJobStatus
from app.db.models.automation_job import AutomationJob
from app.providers.coding.base import CodeIntelligenceProvider
from app.providers.coding.registry import get_coding_provider
from app.services import audit_service
from app.services.execution_service import run_playwright_execution_for_job
from app.services.failure_analysis_service import analyze_execution_failure
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path
from app.services.patch_validation_service import (
    PatchValidationError,
    summarize_patch_for_persistence,
    validate_repair_patch,
)
from app.services.review_revision_prompt_service import build_review_revision_payload
from app.services.workspace_service import WorkspaceApplyError, apply_generated_patch


def finalize_post_review_execution(
    db: Session,
    job: AutomationJob,
    rex: dict[str, Any],
    *,
    actor_id: str,
    audit_step: str,
) -> None:
    """Persist execution result after a review-triggered rerun; set status from outcome."""
    aid = actor_id.strip() or job.requested_by
    job.execution_result_json = {**rex, "after_review_rerun": True}

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REEXECUTION_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name=audit_step,
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "success": bool(rex.get("success")),
            "exit_code": rex.get("exit_code"),
            "context": "post_review",
        },
    )

    if rex.get("success"):
        job.status = AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
        job.blocked_reason = None
        job.failure_analysis_json = None
        db.flush()
        return

    fa = analyze_execution_failure(rex)
    job.failure_analysis_json = fa
    if fa.get("needs_human_input"):
        job.status = AutomationJobStatus.AWAITING_HUMAN_INPUT.value
        job.blocked_reason = (
            str(fa.get("clarification_question") or fa.get("root_cause_summary") or "Human input required")
        )[:2048]
    else:
        job.status = AutomationJobStatus.FAILED.value
        notes = rex.get("notes") or []
        job.blocked_reason = (str(notes[0]) if notes else "Execution failed after review")[:2048]
    db.flush()


def apply_review_revision_and_execute(
    db: Session,
    job: AutomationJob,
    *,
    instruction_text: str,
    actor_id: str,
    provider: CodeIntelligenceProvider | None = None,
    subprocess_run: Any | None = None,
) -> None:
    """Assume job is ``revising_after_review``; validate/apply revision patch and re-run tests."""
    aid = actor_id.strip() or job.requested_by
    p = provider or get_coding_provider()
    payload = build_review_revision_payload(job, instruction_text)
    raw = p.revise_after_review(payload)

    if raw.get("skipped"):
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = str(raw.get("reason") or "Revision was not produced")[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PATCH_VALIDATION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="review_revision",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"stage": "provider_skip", "reason": job.blocked_reason[:500]},
        )
        db.flush()
        return

    try:
        validate_repair_patch(raw, job)
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
            step_name="review_revision",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": msg[:500]},
        )
        db.flush()
        return

    try:
        root = resolve_repo_path(job.repo_path)
        apply_result = apply_generated_patch(root, raw["generated_files"])
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
            step_name="review_revision",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": msg[:500]},
        )
        db.flush()
        return

    summary = summarize_patch_for_persistence(raw)
    summary["apply_result"] = apply_result
    summary["provider"] = p.name
    summary["after_review_revision"] = True
    job.generated_patch_json = summary

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_REVISION_APPLIED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="review_revision",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"files": len(summary.get("generated_files") or [])},
    )
    db.flush()

    rex = run_playwright_execution_for_job(job, subprocess_run=subprocess_run)
    finalize_post_review_execution(db, job, rex, actor_id=aid, audit_step="review_revision")


def apply_review_revision_with_external_patch(
    db: Session,
    job: AutomationJob,
    *,
    instruction_text: str,
    actor_id: str,
    raw_patch: dict[str, Any],
    subprocess_run: Any | None = None,
    provider_label: str = "external",
) -> None:
    """
    Like :func:`apply_review_revision_and_execute` but uses a pre-built ``raw_patch`` dict
    (e.g. from disk after an external coding engine) instead of calling a coding provider.
    """
    aid = actor_id.strip() or job.requested_by
    inst_preview = str(instruction_text or "").strip()[:200]

    try:
        validate_repair_patch(raw_patch, job)
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
            step_name="review_revision",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={
                "message": msg[:500],
                "source": "external_patch",
                "instruction_preview": inst_preview,
            },
        )
        db.flush()
        return

    try:
        root = resolve_repo_path(job.repo_path)
        apply_result = apply_generated_patch(root, raw_patch["generated_files"])
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
            step_name="review_revision",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={
                "message": msg[:500],
                "source": "external_patch",
                "instruction_preview": inst_preview,
            },
        )
        db.flush()
        return

    summary = summarize_patch_for_persistence(raw_patch)
    summary["apply_result"] = apply_result
    summary["provider"] = provider_label
    summary["after_review_revision"] = True
    job.generated_patch_json = summary

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_REVISION_APPLIED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="review_revision",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "files": len(summary.get("generated_files") or []),
            "source": "external_patch",
            "instruction_preview": inst_preview,
        },
    )
    db.flush()

    rex = run_playwright_execution_for_job(job, subprocess_run=subprocess_run)
    finalize_post_review_execution(db, job, rex, actor_id=aid, audit_step="review_revision")


def rerun_execution_after_manual_ack(
    db: Session,
    job: AutomationJob,
    *,
    actor_id: str,
    subprocess_run: Any | None = None,
) -> None:
    """Re-run Playwright only (no provider); same finalize rules as revision re-exec."""
    aid = actor_id.strip() or job.requested_by
    rex = run_playwright_execution_for_job(job, subprocess_run=subprocess_run)
    finalize_post_review_execution(db, job, rex, actor_id=aid, audit_step="manual_edit_ack")
