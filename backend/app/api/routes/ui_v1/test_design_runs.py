"""UI v1 QSwarm-first test-design workspace routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.connectors.jira_client import JiraClient
from app.core.config import get_settings
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.ui_v1_contract import UiTestDesignRunDetail
from app.schemas.test_design_workspace import (
    UiWorkspaceApprove,
    UiWorkspacePlanRevision,
    UiWorkspaceRevision,
    WorkspaceApproveBody,
)
from app.services import test_design_workspace_service
from app.services.ui_v1_mapper import dict_keys_to_camel

router = APIRouter(tags=["ui-v1-test-design-runs"])


def _jira() -> JiraClient:
    return JiraClient(get_settings())


def _value_error_http(e: ValueError) -> HTTPException:
    msg = str(e)
    code = msg
    status_code = status.HTTP_400_BAD_REQUEST
    if msg in ("run_not_found",):
        status_code = status.HTTP_404_NOT_FOUND
    elif msg in (
        "plan_not_approved",
        "plan_not_awaiting_approval",
        "plan_not_ready_for_approval",
        "stale_version_not_approvable",
        "no_pending_approval",
        "invalid_run_state",
        "active_run_exists",
        "test_case_already_automated",
    ):
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail=ErrorDetail(code=code, message=msg.replace("_", " ")).model_dump(),
    )


@router.get("/test-design-runs/{run_id}", response_model=UiTestDesignRunDetail, responses={404: {"model": ErrorResponse}})
def ui_get_test_design_run(run_id: uuid.UUID, db: DbSession):
    try:
        detail = test_design_workspace_service.get_run_detail_for_ui(db, run_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        ) from None
    return dict_keys_to_camel(detail)


@router.post("/test-design-runs/{run_id}/analyze", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def ui_analyze_requirements(run_id: uuid.UUID, db: DbSession, actor_id: str = "qswarm-web"):
    try:
        test_design_workspace_service.analyze_requirements(db, run_id, actor_id=actor_id)
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    return dict_keys_to_camel(test_design_workspace_service.get_analysis_for_ui(db, run_id) or {})


@router.get("/test-design-runs/{run_id}/analysis", responses={404: {"model": ErrorResponse}})
def ui_get_analysis(run_id: uuid.UUID, db: DbSession):
    data = test_design_workspace_service.get_analysis_for_ui(db, run_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Requirement analysis not found").model_dump(),
        )
    return dict_keys_to_camel(data)


@router.post("/test-design-runs/{run_id}/prepare-plan", responses={400: {"model": ErrorResponse}})
def ui_prepare_plan(run_id: uuid.UUID, db: DbSession, actor_id: str = "qswarm-web"):
    try:
        test_design_workspace_service.prepare_test_design_plan(db, run_id, actor_id=actor_id)
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    return dict_keys_to_camel(test_design_workspace_service.get_plan_for_ui(db, run_id) or {})


@router.get("/test-design-runs/{run_id}/plan", responses={404: {"model": ErrorResponse}})
def ui_get_plan(run_id: uuid.UUID, db: DbSession):
    data = test_design_workspace_service.get_plan_for_ui(db, run_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Test-design plan not found").model_dump(),
        )
    return dict_keys_to_camel(data)


@router.post("/test-design-runs/{run_id}/approve-plan", responses={409: {"model": ErrorResponse}})
def ui_approve_plan(run_id: uuid.UUID, db: DbSession, actor_id: str = "qswarm-web"):
    try:
        test_design_workspace_service.approve_test_design_plan(db, run_id, actor_id=actor_id)
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    return dict_keys_to_camel(test_design_workspace_service.get_run_detail_for_ui(db, run_id))


@router.post("/test-design-runs/{run_id}/request-plan-revision", responses={409: {"model": ErrorResponse}})
def ui_request_plan_revision(run_id: uuid.UUID, body: UiWorkspacePlanRevision, db: DbSession):
    try:
        test_design_workspace_service.request_test_design_plan_revision(db, run_id, body.to_legacy())
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    return dict_keys_to_camel(test_design_workspace_service.get_run_detail_for_ui(db, run_id))


@router.post("/test-design-runs/{run_id}/generate-test-cases", responses={409: {"model": ErrorResponse}})
def ui_generate_test_cases(run_id: uuid.UUID, db: DbSession, actor_id: str = "qswarm-web"):
    try:
        result = test_design_workspace_service.generate_test_cases(
            db, _jira(), get_settings(), run_id, actor_id=actor_id
        )
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    payload = test_design_workspace_service.get_review_data_for_ui(db, run_id)
    payload["generation"] = result
    return dict_keys_to_camel(payload)


@router.get("/test-design-runs/{run_id}/review-data", responses={404: {"model": ErrorResponse}})
def ui_get_review_data(run_id: uuid.UUID, db: DbSession):
    try:
        data = test_design_workspace_service.get_review_data_for_ui(db, run_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Workflow run not found").model_dump(),
        ) from None
    return dict_keys_to_camel(data)


@router.post("/test-design-runs/{run_id}/request-revision", responses={409: {"model": ErrorResponse}})
def ui_request_revision(run_id: uuid.UUID, body: UiWorkspaceRevision, db: DbSession):
    try:
        result = test_design_workspace_service.request_test_case_revision(db, run_id, body.to_legacy())
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    return dict_keys_to_camel(result)


@router.post("/test-design-runs/{run_id}/approve", responses={409: {"model": ErrorResponse}})
def ui_approve_test_design(run_id: uuid.UUID, body: UiWorkspaceApprove, db: DbSession):
    try:
        test_design_workspace_service.approve_test_design(
            db,
            run_id,
            WorkspaceApproveBody(actor_id=body.actor_id, notes=body.notes),
        )
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    return dict_keys_to_camel(test_design_workspace_service.get_run_detail_for_ui(db, run_id))


@router.post("/test-design-runs/{run_id}/publish", responses={409: {"model": ErrorResponse}})
def ui_publish_test_design(run_id: uuid.UUID, db: DbSession, actor_id: str = "qswarm-web"):
    try:
        result = test_design_workspace_service.publish_test_design(db, run_id, actor_id=actor_id)
    except ValueError as e:
        raise _value_error_http(e) from e
    db.commit()
    detail = test_design_workspace_service.get_run_detail_for_ui(db, run_id)
    detail["publication_result"] = result
    return dict_keys_to_camel(detail)
