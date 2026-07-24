"""UI v1 Jira story intake routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession
from app.connectors.jira_client import JiraClient
from app.core.config import get_settings
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.test_design_workspace import TestDesignRunCreateBody, UiBulkTestDesignRunCreate, UiTestDesignRunCreate
from app.services import test_design_workspace_service
from app.services.ui_v1_mapper import dict_keys_to_camel

router = APIRouter(tags=["ui-v1-stories"])


def _jira() -> JiraClient:
    return JiraClient(get_settings())


@router.get("/stories")
def ui_list_stories(
    db: DbSession,
    project_key: str | None = Query(default=None, alias="projectKey"),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
):
    items = test_design_workspace_service.list_stories_for_ui(
        db, _jira(), project_key=project_key, status=status, q=q, limit=limit
    )
    return {"items": [dict_keys_to_camel(x) for x in items]}


@router.get("/stories/{story_key}", responses={404: {"model": ErrorResponse}})
def ui_get_story(story_key: str, db: DbSession):
    try:
        detail = test_design_workspace_service.get_story_detail_for_ui(db, _jira(), story_key)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message=str(e)[:500]).model_dump(),
        ) from e
    return dict_keys_to_camel(detail)


@router.post("/stories/{story_key}/test-design-runs", responses={409: {"model": ErrorResponse}})
def ui_create_test_design_run(story_key: str, body: UiTestDesignRunCreate, db: DbSession):
    try:
        run = test_design_workspace_service.create_workspace_run(
            db,
            _jira(),
            story_key=story_key,
            body=TestDesignRunCreateBody(initiated_by=body.initiated_by),
        )
    except ValueError as e:
        if str(e) == "active_run_exists":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(code="active_run_exists", message="An active workflow run already exists for this story").model_dump(),
            ) from e
        raise
    db.commit()
    detail = test_design_workspace_service.get_run_detail_for_ui(db, run.id)
    return dict_keys_to_camel(detail)


@router.post("/test-design-runs/bulk")
def ui_bulk_create_test_design_runs(body: UiBulkTestDesignRunCreate, db: DbSession):
    created: list[dict] = []
    errors: list[dict] = []
    for key in body.story_keys:
        try:
            run = test_design_workspace_service.create_workspace_run(
                db,
                _jira(),
                story_key=key,
                body=TestDesignRunCreateBody(initiated_by=body.initiated_by),
            )
            created.append({"storyKey": key.upper(), "workflowRunId": str(run.id)})
        except ValueError as e:
            errors.append({"storyKey": key.upper(), "error": str(e)})
    db.commit()
    return {"created": created, "errors": errors}
