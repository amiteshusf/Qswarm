"""Workflow run API."""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession, JiraClientDep
from app.connectors.jira_client import JiraClientError
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.workflow import (
    WorkflowRunCreateRequest,
    WorkflowRunResponse,
    WorkflowStartResponse,
)
from app.services import workflow_service

router = APIRouter(prefix="/workflow", tags=["workflow"])


@router.post("/runs", response_model=WorkflowRunResponse, status_code=status.HTTP_201_CREATED)
def create_run(body: WorkflowRunCreateRequest, db: DbSession):
    run = workflow_service.create_run(db, body)
    db.commit()
    db.refresh(run)
    return WorkflowRunResponse.model_validate(workflow_service.run_to_response(run))


@router.get(
    "/runs/{run_id}",
    response_model=WorkflowRunResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_run(run_id: uuid.UUID, db: DbSession):
    run = workflow_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        )
    return WorkflowRunResponse.model_validate(workflow_service.run_to_response(run))


@router.post(
    "/runs/{run_id}/start",
    response_model=WorkflowStartResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
def start_run(run_id: uuid.UUID, db: DbSession, jira: JiraClientDep):
    try:
        run = workflow_service.start_run(db, run_id, jira)
    except ValueError as e:
        msg = str(e)
        if msg == "run_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
            ) from e
        if msg == "run_not_pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state", message="Run is not in pending state"
                ).model_dump(),
            ) from e
        if msg == "missing_jira_issue_key":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="invalid_run", message="Run is missing jira_issue_key in graph state"
                ).model_dump(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code="bad_request", message=msg).model_dump(),
        ) from e
    except JiraClientError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    db.commit()
    db.refresh(run)
    return WorkflowStartResponse(run_id=run.id, status=run.status)
