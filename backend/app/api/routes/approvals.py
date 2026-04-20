"""Approval decisions."""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.schemas.approval import ApprovalDecisionRequest, ApprovalResponse
from app.schemas.common import ErrorDetail, ErrorResponse
from app.services import approval_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_approval(approval_id: uuid.UUID, db: DbSession):
    row = approval_service.get_approval(db, approval_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Approval not found").model_dump(),
        )
    return ApprovalResponse.model_validate(row)


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def approve(approval_id: uuid.UUID, body: ApprovalDecisionRequest, db: DbSession):
    try:
        row = approval_service.approve(db, approval_id, actor_id=body.actor_id, notes=body.notes)
    except ValueError as e:
        msg = str(e)
        if msg == "approval_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Approval not found").model_dump(),
            ) from e
        if msg == "approval_not_pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state", message="Approval is not pending"
                ).model_dump(),
            ) from e
        raise
    db.commit()
    db.refresh(row)
    return ApprovalResponse.model_validate(row)


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def reject(approval_id: uuid.UUID, body: ApprovalDecisionRequest, db: DbSession):
    try:
        row = approval_service.reject(db, approval_id, actor_id=body.actor_id, notes=body.notes)
    except ValueError as e:
        msg = str(e)
        if msg == "approval_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Approval not found").model_dump(),
            ) from e
        if msg == "approval_not_pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state", message="Approval is not pending"
                ).model_dump(),
            ) from e
        raise
    db.commit()
    db.refresh(row)
    return ApprovalResponse.model_validate(row)
