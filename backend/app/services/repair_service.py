"""One-step repair: analyze failure, optional patch, single re-execution."""

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
from app.services.patch_validation_service import PatchValidationError, validate_repair_patch
from app.services.repair_prompt_service import build_repair_payload
from app.services.workspace_service import WorkspaceApplyError, apply_generated_patch


def repair_prerequisites_met(job: AutomationJob) -> bool:
    if job.status != AutomationJobStatus.FAILED.value:
        return False
    if not isinstance(job.execution_result_json, dict):
        return False
    if job.execution_result_json.get("success") is True:
        return False
    if not isinstance(job.change_plan_json, dict):
        return False
    fw = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else None
    if not fw or fw.get("framework_type") != "playwright":
        return False
    if job.repo_path is None or not str(job.repo_path).strip():
        return False
    return True


def run_repair(
    db: Session,
    job: AutomationJob,
    *,
    actor_id: str,
    provider: CodeIntelligenceProvider | None = None,
    subprocess_run: Any | None = None,
) -> None:
    """
    Mutate ``job`` in-session: failure analysis, optional repair patch + apply, one re-run.

    Raises:
        ValueError: ``repair_already_attempted`` or ``repair_prerequisites_missing``.
    """
    if job.repair_result_json is not None:
        raise ValueError("repair_already_attempted")
    if not repair_prerequisites_met(job):
        raise ValueError("repair_prerequisites_missing")

    aid = actor_id.strip() or job.requested_by
    ex = job.execution_result_json
    assert isinstance(ex, dict)

    fa = analyze_execution_failure(ex)
    job.failure_analysis_json = fa
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_FAILURE_ANALYZED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="repair",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "failure_type": fa.get("failure_type"),
            "repairable": fa.get("repairable"),
            "needs_human_input": fa.get("needs_human_input"),
        },
    )
    db.flush()

    def _skip_result(
        *,
        attempted: bool,
        skipped_reason: str,
        notes: list[str],
        provider_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        d: dict[str, Any] = {
            "attempted": attempted,
            "provider": provider_name,
            "repair_notes": notes[:20],
            "applied_files": [],
            "reexecution_success": None,
            "skipped_reason": skipped_reason,
        }
        if extra:
            d.update(extra)
        return d

    if fa.get("needs_human_input"):
        job.repair_result_json = _skip_result(
            attempted=False,
            skipped_reason="human_input",
            notes=["Repair skipped: human clarification required"],
        )
        job.status = AutomationJobStatus.AWAITING_HUMAN_INPUT.value
        job.blocked_reason = (
            str(fa.get("clarification_question") or fa.get("root_cause_summary") or "Human input required")
        )[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPAIR_SKIPPED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="repair",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"reason": "human_input"},
        )
        db.flush()
        return

    if not fa.get("repairable") or fa.get("suggested_action") != "repair_patch":
        job.repair_result_json = _skip_result(
            attempted=False,
            skipped_reason="not_repairable",
            notes=["Failure not classified as auto-repairable"],
        )
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = str(fa.get("root_cause_summary") or "Not repairable")[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPAIR_SKIPPED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="repair",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"reason": "not_repairable"},
        )
        db.flush()
        return

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REPAIR_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="repair",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"failure_type": fa.get("failure_type")},
    )
    db.flush()

    p = provider or get_coding_provider()
    payload = build_repair_payload(job, fa)
    raw = p.suggest_repair(payload)

    if raw.get("skipped"):
        job.repair_result_json = _skip_result(
            attempted=False,
            skipped_reason="provider_skipped",
            notes=[str(raw.get("reason") or "provider skipped repair")],
            provider_name=p.name,
        )
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = str(raw.get("reason") or "Repair not applied")[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPAIR_SKIPPED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="repair",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"reason": "provider_skipped"},
        )
        db.flush()
        return

    try:
        validate_repair_patch(raw, job)
    except PatchValidationError as e:
        job.repair_result_json = {
            "attempted": True,
            "provider": p.name,
            "repair_notes": ["Repair patch failed validation"],
            "applied_files": [],
            "reexecution_success": False,
            "validation_error": e.message[:1024],
        }
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = e.message[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPAIR_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="repair",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"stage": "validation"},
        )
        db.flush()
        return

    try:
        root = resolve_repo_path(job.repo_path)
        apply_generated_patch(root, raw["generated_files"])
    except (WorkspaceApplyError, FrameworkScanError) as e:
        msg = getattr(e, "message", str(e))[:2048]
        job.repair_result_json = {
            "attempted": True,
            "provider": p.name,
            "repair_notes": ["Repair patch apply failed"],
            "applied_files": [],
            "reexecution_success": False,
            "apply_error": msg[:1024],
        }
        job.status = AutomationJobStatus.FAILED.value
        job.blocked_reason = msg
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPAIR_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="repair",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"stage": "apply"},
        )
        db.flush()
        return

    applied = [
        {"path": str(item.get("path")), "action": item.get("action")}
        for item in (raw.get("generated_files") or [])
        if isinstance(item, dict) and item.get("path")
    ]

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REPAIR_APPLIED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="repair",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"files": len(applied)},
    )
    db.flush()

    rex = run_playwright_execution_for_job(job, subprocess_run=subprocess_run)
    job.execution_result_json = {**rex, "after_repair_rerun": True}

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REEXECUTION_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="repair",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "success": bool(rex.get("success")),
            "exit_code": rex.get("exit_code"),
            "duration_ms": rex.get("duration_ms"),
        },
    )

    job.repair_result_json = {
        "attempted": True,
        "provider": p.name,
        "repair_notes": [str(x) for x in (raw.get("generation_notes") or []) if isinstance(x, str)][:20],
        "applied_files": applied[:30],
        "reexecution_success": bool(rex.get("success")),
    }

    if rex.get("success"):
        job.status = AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
        job.blocked_reason = None
    else:
        job.status = AutomationJobStatus.FAILED.value
        n = rex.get("notes") or []
        job.blocked_reason = (str(n[0]) if n else "Repair re-run failed")[:2048]

    db.flush()
