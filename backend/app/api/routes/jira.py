"""Jira read endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession, JiraClientDep, SettingsDep
from app.connectors.jira_client import JiraClientError
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.jira import (
    JiraConnectionTestResponse,
    JiraIssueFetchResponse,
    JiraSearchHit,
    JiraSearchRequest,
    JiraSearchResponse,
    JiraStoryResponse,
)
from app.schemas.jira_pickup import JiraPickupPollRequest, JiraPickupPollResponse
from app.services import jira_polling_service

router = APIRouter(prefix="/jira", tags=["jira"])

CONNECTION_TEST_ISSUE_KEY = "NSP-677"


def _http_status_for_jira_error(e: JiraClientError) -> int:
    sc = e.status_code
    if sc in (400, 401, 403, 404):
        return sc
    return status.HTTP_502_BAD_GATEWAY


@router.get("/connection-test", response_model=JiraConnectionTestResponse)
def jira_connection_test(settings: SettingsDep, jira: JiraClientDep):
    """
    Verify Jira configuration and (when not in stub mode) fetch a sample issue via the real API.

    Does not return credentials or auth headers. Uses issue ``NSP-677`` when calling real Jira.
    """
    base = settings.jira_base_url.strip() or None
    if settings.effective_jira_stub:
        if settings.jira_use_stub:
            stub_msg = "JIRA_USE_STUB is true; real Jira connection was not tested"
        else:
            stub_msg = (
                "Jira is not fully configured; real Jira connection was not tested "
                "(set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN)"
            )
        return JiraConnectionTestResponse(
            ok=False,
            mode="stub",
            message=stub_msg,
            base_url=base,
        )

    try:
        jira.get_issue(CONNECTION_TEST_ISSUE_KEY)
    except JiraClientError as e:
        return JiraConnectionTestResponse(
            ok=False,
            mode="real",
            message="Jira connection failed",
            base_url=base,
            sample_issue_key=CONNECTION_TEST_ISSUE_KEY,
            error=str(e)[:800],
        )

    return JiraConnectionTestResponse(
        ok=True,
        mode="real",
        message="Jira connection successful",
        base_url=base,
        sample_issue_key=CONNECTION_TEST_ISSUE_KEY,
    )


@router.get(
    "/issues/{issue_key}",
    response_model=JiraIssueFetchResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
def get_issue_normalized(issue_key: str, jira: JiraClientDep):
    """Fetch one issue and return key fields only (no raw Jira JSON)."""
    try:
        data = jira.get_issue(issue_key)
    except JiraClientError as e:
        raise HTTPException(
            status_code=_http_status_for_jira_error(e),
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    return JiraIssueFetchResponse(
        ok=True,
        issue_key=data["issue_key"],
        summary=data.get("summary"),
        description=data.get("description"),
        status=data.get("status"),
        issue_type=data.get("issue_type"),
        assignee=data.get("assignee"),
        reporter=data.get("reporter"),
        priority=data.get("priority"),
    )


@router.get(
    "/story/{issue_key}",
    response_model=JiraStoryResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
def get_story(issue_key: str, jira: JiraClientDep):
    try:
        data = jira.get_issue(issue_key)
    except JiraClientError as e:
        raise HTTPException(
            status_code=_http_status_for_jira_error(e),
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    return JiraStoryResponse(
        issue_key=data["issue_key"],
        issue_id=data.get("issue_id"),
        summary=data.get("summary") or "",
        description=data.get("description"),
        issue_type=data.get("issue_type"),
        priority=data.get("priority"),
        status=data.get("status"),
        assignee=data.get("assignee"),
        reporter=data.get("reporter"),
        labels=list(data.get("labels") or []),
        raw_available=bool(data.get("raw_payload")),
    )


@router.post(
    "/search",
    response_model=JiraSearchResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
def search_issues(body: JiraSearchRequest, jira: JiraClientDep):
    try:
        result = jira.search_issues(body.jql, max_results=body.max_results)
    except JiraClientError as e:
        raise HTTPException(
            status_code=_http_status_for_jira_error(e),
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    hits = [
        JiraSearchHit(issue_key=x["issue_key"], summary=x.get("summary") or "", status=x.get("status"))
        for x in result.get("issues") or []
    ]
    return JiraSearchResponse(issues=hits, total=result.get("total"))


@router.post(
    "/pickup/poll",
    response_model=JiraPickupPollResponse,
    summary="Manual Jira pickup poll (Sprint 1)",
    description=(
        "Search Jira for issues with label ``qswarm-test-design`` (Story/Task), "
        "run preflight, create and start Sprint 1 workflow runs for eligible issues. "
        "For controlled testing; no background scheduler."
    ),
)
def jira_pickup_poll(
    db: DbSession,
    jira: JiraClientDep,
    body: JiraPickupPollRequest = JiraPickupPollRequest(),
):
    return jira_polling_service.run_pickup_poll(db, jira, limit=body.limit)
