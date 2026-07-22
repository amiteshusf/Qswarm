"""Human approval decisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.constants import (
    ActorType,
    ApprovalStatus,
    AuditEventType,
    WorkflowRunStatus,
)
from app.db.models.approval import Approval
from app.db.models.workflow_run import WorkflowRun
from app.services import audit_service


def get_approval(db: Session, approval_id: uuid.UUID) -> Approval | None:
    return db.get(Approval, approval_id)


def approve(
    db: Session,
    approval_id: uuid.UUID,
    *,
    actor_id: str,
    notes: str | None,
) -> Approval:
    row = db.get(Approval, approval_id)
    if row is None:
        raise ValueError("approval_not_found")
    if row.status != ApprovalStatus.PENDING.value:
        raise ValueError("approval_not_pending")

    now = datetime.now(timezone.utc)
    row.status = ApprovalStatus.APPROVED.value
    row.decided_by = actor_id
    row.decided_at = now
    row.decision_notes = notes

    run = db.get(WorkflowRun, row.workflow_run_id)
    if run:
        run.status = WorkflowRunStatus.COMPLETED.value
        run.completed_at = now
        run.current_step = "completed"
        run.error_message = None

    audit_service.write_audit(
        db,
        event_type=AuditEventType.APPROVAL_APPROVED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id,
        workflow_run_id=row.workflow_run_id,
        step_name="approval",
        entity_type="approval",
        entity_id=str(row.id),
        payload={"notes": notes},
    )
    if run:
        audit_service.write_audit(
            db,
            event_type=AuditEventType.WORKFLOW_COMPLETED.value,
            actor_type=ActorType.USER.value,
            actor_id=actor_id,
            workflow_run_id=run.id,
            step_name="completed",
            entity_type="workflow_run",
            entity_id=str(run.id),
            payload={"via": "approval"},
        )
    from app.services.test_case_registry_service import materialize_test_cases_from_approved_workflow

    try:
        materialize_test_cases_from_approved_workflow(db, row, actor_id=actor_id, auto_publish=True)
    except Exception:
        pass
    db.flush()
    return row


def reject(
    db: Session,
    approval_id: uuid.UUID,
    *,
    actor_id: str,
    notes: str | None,
) -> Approval:
    row = db.get(Approval, approval_id)
    if row is None:
        raise ValueError("approval_not_found")
    if row.status != ApprovalStatus.PENDING.value:
        raise ValueError("approval_not_pending")

    now = datetime.now(timezone.utc)
    row.status = ApprovalStatus.REJECTED.value
    row.decided_by = actor_id
    row.decided_at = now
    row.decision_notes = notes

    run = db.get(WorkflowRun, row.workflow_run_id)
    if run:
        run.status = WorkflowRunStatus.REJECTED.value
        run.completed_at = now
        run.current_step = "rejected"

    audit_service.write_audit(
        db,
        event_type=AuditEventType.APPROVAL_REJECTED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id,
        workflow_run_id=row.workflow_run_id,
        step_name="approval",
        entity_type="approval",
        entity_id=str(row.id),
        payload={"notes": notes},
    )
    db.flush()
    return row
