"""UI v1 test case registry / automation backlog routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.test_case_registry import UiTestCaseAutomate
from app.services import test_case_publication_service, test_case_registry_service
from app.services.ui_v1_mapper import dict_keys_to_camel
from app.services.ui_v1_sessions import build_session_detail_json_for_ui

router = APIRouter(tags=["ui-v1-test-cases"])


@router.get("/test-cases")
def ui_list_test_cases(
    db: DbSession,
    status: str | None = Query(default=None, description="Filter e.g. automation_ready"),
    workflow_run_id: uuid.UUID | None = Query(default=None, alias="workflowRunId"),
    source_story_key: str | None = Query(default=None, alias="sourceStoryKey"),
    limit: int = Query(default=100, ge=1, le=500),
):
    items = test_case_registry_service.list_test_cases_for_api(
        db,
        status=status,
        workflow_run_id=workflow_run_id,
        source_story_key=source_story_key,
        limit=limit,
    )
    return {"items": [dict_keys_to_camel(x) for x in items]}


@router.get("/test-cases/{record_id}", responses={404: {"model": ErrorResponse}})
def ui_get_test_case(record_id: uuid.UUID, db: DbSession):
    row = test_case_registry_service.get_test_case_for_api(db, record_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Test case not found").model_dump(),
        )
    return dict_keys_to_camel(row)


@router.post("/test-cases/{record_id}/publish", responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def ui_publish_test_case(record_id: uuid.UUID, db: DbSession, actor_id: str = Query(default="qswarm-web")):
    try:
        test_case_publication_service.publish_test_case_record(db, record_id, actor_id=actor_id)
    except ValueError as e:
        if str(e) == "test_case_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Test case not found").model_dump(),
            ) from e
        raise
    db.commit()
    row = test_case_registry_service.get_test_case_for_api(db, record_id)
    return dict_keys_to_camel(row or {})


@router.post("/test-cases/{record_id}/automate", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def ui_automate_test_case(record_id: uuid.UUID, body: UiTestCaseAutomate, db: DbSession):
    try:
        sess = test_case_registry_service.create_automation_session_from_test_case(
            db, record_id, body.to_legacy()
        )
    except ValueError as e:
        msg = str(e)
        if msg == "test_case_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Test case not found").model_dump(),
            ) from e
        if msg in ("test_case_not_published", "test_case_already_automated", "test_case_missing_external_id"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(code=msg, message=msg.replace("_", " ")).model_dump(),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code="invalid_request", message=msg).model_dump(),
        ) from e
    db.commit()
    return build_session_detail_json_for_ui(db, sess.id)
