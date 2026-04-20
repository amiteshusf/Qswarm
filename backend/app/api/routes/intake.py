"""Story intake from Jira (no workflow run required)."""

from fastapi import APIRouter, HTTPException, status

from app.agents.story_intake_agent import run_intake
from app.api.deps import JiraClientDep
from app.connectors.jira_client import JiraClientError
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.intake import IntakeFromJiraResponse, StoryIntakeArtifactContent, TestableCriterion

router = APIRouter(prefix="/intake", tags=["intake"])


@router.post(
    "/from-jira/{issue_key}",
    response_model=IntakeFromJiraResponse,
    responses={502: {"model": ErrorResponse}},
)
def intake_from_jira(issue_key: str, jira: JiraClientDep):
    try:
        data = jira.get_issue(issue_key)
    except JiraClientError as e:
        code = status.HTTP_502_BAD_GATEWAY
        if e.status_code == 404:
            code = status.HTTP_404_NOT_FOUND
        raise HTTPException(
            status_code=code,
            detail=ErrorDetail(code="jira_error", message=str(e)).model_dump(),
        ) from e

    fields = {
        "issue_key": data["issue_key"],
        "summary": data.get("summary") or "",
        "description": data.get("description") or "",
        "labels": data.get("labels") or [],
        "priority": data.get("priority") or "",
        "issue_type": data.get("issue_type") or "",
        "status": data.get("status") or "",
    }
    raw = run_intake(fields)
    criteria = [
        TestableCriterion(text=c["text"], source=c.get("source", "inferred_from_description"))
        if isinstance(c, dict)
        else TestableCriterion(text=str(c))
        for c in (raw.get("testable_acceptance_criteria") or [])
    ]
    intake = StoryIntakeArtifactContent(
        story_key=raw["story_key"],
        business_goal=raw["business_goal"],
        in_scope=list(raw.get("in_scope") or []),
        out_of_scope=list(raw.get("out_of_scope") or []),
        assumptions=list(raw.get("assumptions") or []),
        risks=list(raw.get("risks") or []),
        open_questions=list(raw.get("open_questions") or []),
        testable_acceptance_criteria=criteria,
        recommended_test_focus=list(raw.get("recommended_test_focus") or []),
    )
    return IntakeFromJiraResponse(issue_key=data["issue_key"], intake=intake)
