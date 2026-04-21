"""Workflow run API."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DbSession, JiraClientDep, SettingsDep
from app.connectors.jira_client import JiraClientError
from app.db.models.jira_generated_test_case import JiraGeneratedTestCase
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.workflow import (
    JiraGeneratedDraftCaseResponse,
    TestDesignEvolutionResponse,
    TestDesignFeedbackItem,
    TestDesignFeedbackListResponse,
    TestDesignFeedbackRequest,
    TestDesignVersionItem,
    TestDesignVersionsListResponse,
    WorkflowJiraDraftTestCasesResponse,
    WorkflowRunCreateRequest,
    WorkflowRunResponse,
    WorkflowStartResponse,
)
from app.services import test_design_evolution_service, workflow_service

router = APIRouter(prefix="/workflow", tags=["workflow"])


def _evolution_value_error_detail(exc: ValueError) -> tuple[int, str, str]:
    msg = str(exc)
    mapping: dict[str, tuple[int, str, str]] = {
        "run_not_found": (status.HTTP_404_NOT_FOUND, "not_found", "Workflow run not found"),
        "invalid_run_state": (status.HTTP_409_CONFLICT, "invalid_state", "Refine/regenerate is only allowed while awaiting approval"),
        "no_pending_approval": (status.HTTP_409_CONFLICT, "no_pending_approval", "No pending approval for this run"),
        "no_test_design_version": (status.HTTP_409_CONFLICT, "no_test_design_version", "Test design version metadata is missing"),
        "current_artifact_missing": (status.HTTP_409_CONFLICT, "artifact_missing", "Current test design artifact is missing"),
        "missing_jira_issue_key": (status.HTTP_400_BAD_REQUEST, "invalid_run", "Run is missing jira_issue_key in graph state"),
        "missing_intake_artifact": (status.HTTP_400_BAD_REQUEST, "invalid_run", "Run is missing intake_artifact_id for regenerate"),
        "intake_artifact_missing": (status.HTTP_400_BAD_REQUEST, "invalid_run", "Story intake artifact not found"),
        "no_cases_after_evolution": (status.HTTP_400_BAD_REQUEST, "no_cases", "Evolution produced no publishable test cases"),
    }
    if msg in mapping:
        code, c, m = mapping[msg]
        return code, c, m
    return status.HTTP_400_BAD_REQUEST, "bad_request", msg


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


@router.get(
    "/runs/{run_id}/generated-test-cases",
    response_model=WorkflowJiraDraftTestCasesResponse,
    responses={404: {"model": ErrorResponse}},
    summary="List Jira draft Tasks published from Sprint 1 test design",
)
def list_generated_jira_test_cases(run_id: uuid.UUID, db: DbSession):
    run = workflow_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        )
    rows = db.scalars(
        select(JiraGeneratedTestCase)
        .where(JiraGeneratedTestCase.workflow_run_id == run_id)
        .order_by(JiraGeneratedTestCase.created_at.asc())
    ).all()
    return WorkflowJiraDraftTestCasesResponse(
        workflow_run_id=run_id,
        items=[JiraGeneratedDraftCaseResponse.model_validate(r) for r in rows],
    )


@router.get(
    "/runs/{run_id}/test-design/versions",
    response_model=TestDesignVersionsListResponse,
    responses={404: {"model": ErrorResponse}},
    summary="List internal test design versions for a workflow run",
)
def list_test_design_versions(run_id: uuid.UUID, db: DbSession):
    run = workflow_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        )
    raw = test_design_evolution_service.list_versions_for_api(db, run_id)
    return TestDesignVersionsListResponse(
        workflow_run_id=run_id,
        items=[TestDesignVersionItem.model_validate(x) for x in raw],
    )


@router.get(
    "/runs/{run_id}/test-design/feedback",
    response_model=TestDesignFeedbackListResponse,
    responses={404: {"model": ErrorResponse}},
    summary="List recorded test design feedback rows",
)
def list_test_design_feedback(run_id: uuid.UUID, db: DbSession):
    run = workflow_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        )
    raw = test_design_evolution_service.list_feedback_for_api(db, run_id)
    return TestDesignFeedbackListResponse(
        workflow_run_id=run_id,
        items=[TestDesignFeedbackItem.model_validate(x) for x in raw],
    )


@router.post(
    "/runs/{run_id}/test-design/refine",
    response_model=TestDesignEvolutionResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="Refine draft test design from feedback (awaiting approval only)",
)
def refine_test_design(
    run_id: uuid.UUID,
    body: TestDesignFeedbackRequest,
    db: DbSession,
    jira: JiraClientDep,
    settings: SettingsDep,
):
    try:
        out = test_design_evolution_service.evolve_test_design(
            db,
            jira,
            settings,
            workflow_run_id=run_id,
            action="refine",
            actor_id=body.actor_id,
            feedback_text=body.feedback_text,
            target_scope=body.target_scope,
        )
    except ValueError as e:
        db.commit()
        code, c, m = _evolution_value_error_detail(e)
        raise HTTPException(
            status_code=code,
            detail=ErrorDetail(code=c, message=m).model_dump(),
        ) from e
    except JiraClientError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    db.commit()
    return TestDesignEvolutionResponse.model_validate(out)


@router.post(
    "/runs/{run_id}/test-design/regenerate",
    response_model=TestDesignEvolutionResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
    summary="Regenerate draft test design from story + feedback (awaiting approval only)",
)
def regenerate_test_design(
    run_id: uuid.UUID,
    body: TestDesignFeedbackRequest,
    db: DbSession,
    jira: JiraClientDep,
    settings: SettingsDep,
):
    try:
        out = test_design_evolution_service.evolve_test_design(
            db,
            jira,
            settings,
            workflow_run_id=run_id,
            action="regenerate",
            actor_id=body.actor_id,
            feedback_text=body.feedback_text,
            target_scope=body.target_scope,
        )
    except ValueError as e:
        db.commit()
        code, c, m = _evolution_value_error_detail(e)
        raise HTTPException(
            status_code=code,
            detail=ErrorDetail(code=c, message=m).model_dump(),
        ) from e
    except JiraClientError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    db.commit()
    return TestDesignEvolutionResponse.model_validate(out)


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
