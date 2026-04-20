"""Audit log read API."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.db.models.audit_log import AuditLog
from app.schemas.audit import AuditLogResponse
from app.schemas.common import ErrorDetail, ErrorResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get(
    "/workflow/{run_id}",
    response_model=list[AuditLogResponse],
    responses={404: {"model": ErrorResponse}},
)
def list_audit_for_run(run_id: uuid.UUID, db: DbSession):
    from app.db.models.workflow_run import WorkflowRun

    if db.get(WorkflowRun, run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        )

    rows = db.execute(
        select(AuditLog)
        .where(AuditLog.workflow_run_id == run_id)
        .order_by(AuditLog.created_at.asc())
    ).scalars().all()
    return [AuditLogResponse.model_validate(r) for r in rows]
