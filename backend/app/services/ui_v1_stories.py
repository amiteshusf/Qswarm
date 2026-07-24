"""BFF mapper for GET /api/v1/stories — stable UI contract."""

from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.agents.story_intake_agent import run_intake
from app.connectors.jira_client import JiraClient
from app.core.config import Settings
from app.schemas.ui_v1_stories import AcceptanceCriteriaStatus, StoryReadiness, UiStoryListResponse, UiStorySummary
from app.services import test_design_workspace_service

_AC_LINE_RE = re.compile(
    r"(?i)(acceptance criteria|given\b|when\b|then\b|must\b|should\b|user can\b|system shall\b)"
)


def project_key_from_story(story_key: str) -> str:
    key = story_key.strip().upper()
    if "-" in key:
        return key.split("-", 1)[0]
    return key


def build_jira_browse_url(settings: Settings, story_key: str) -> str | None:
    base = (settings.jira_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/browse/{story_key.strip().upper()}"


def normalize_assignee(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"unassigned", "none", "null"}:
        return None
    return text


def _compute_readiness_signals(
    *,
    summary: str,
    description: str,
) -> tuple[StoryReadiness, AcceptanceCriteriaStatus, list[str]]:
    """Derive readiness enums and missing-information hints from story text."""
    summary = (summary or "").strip()
    description = (description or "").strip()
    missing: list[str] = []

    if not summary and not description:
        return "missing_ac", "missing_ac", ["No summary or description available"]

    if not description:
        missing.append("Detailed description is missing")

    intake = run_intake(
        {
            "issue_key": "TEMP",
            "summary": summary,
            "description": description,
            "labels": [],
            "priority": "unspecified",
            "issue_type": "Story",
        }
    )
    criteria = intake.get("testable_acceptance_criteria") or []
    if not isinstance(criteria, list):
        criteria = []

    sources = {str(c.get("source") or "") for c in criteria if isinstance(c, dict)}
    explicit_ac_in_text = bool(_AC_LINE_RE.search(f"{summary}\n{description}"))

    if not description and not explicit_ac_in_text:
        if not summary:
            return "missing_ac", "missing_ac", missing or ["No acceptance criteria found"]
        return "missing_ac", "missing_ac", missing + ["No explicit acceptance criteria found"]

    if sources == {"summary_fallback"} and not explicit_ac_in_text:
        if not description:
            return "partial", "missing_ac", missing + ["Acceptance criteria inferred only from summary"]
        return "partial", "partial", missing + ["Few explicit acceptance criteria were found"]

    if len(criteria) >= 2 or explicit_ac_in_text or "inferred_from_description" in sources:
        ac_status: AcceptanceCriteriaStatus = "ready"
        readiness: StoryReadiness = "ready" if description else "partial"
        if not description:
            missing.append("Detailed description is missing")
        return readiness, ac_status, missing

    return "partial", "partial", missing + ["Few explicit acceptance criteria were found"]


def map_issue_to_ui_story(
    issue: dict[str, Any],
    *,
    settings: Settings,
    active_run_id: str | None,
) -> UiStorySummary:
    story_key = str(issue.get("issue_key") or "").strip().upper()
    summary = str(issue.get("summary") or "").strip()
    description = str(issue.get("description") or "").strip()
    readiness, ac_status, missing_information = _compute_readiness_signals(
        summary=summary,
        description=description,
    )
    has_active_run = active_run_id is not None

    return UiStorySummary(
        story_key=story_key,
        title=summary,
        description=description,
        status=issue.get("status"),
        sprint=issue.get("sprint"),
        project_key=project_key_from_story(story_key),
        assignee=normalize_assignee(issue.get("assignee")),
        readiness=readiness,
        acceptance_criteria_status=ac_status,
        missing_information=missing_information,
        has_active_run=has_active_run,
        active_run_id=active_run_id,
        jira_url=build_jira_browse_url(settings, story_key),
    )


def build_story_list_for_ui(
    db: Session,
    jira: JiraClient,
    settings: Settings,
    *,
    project_key: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> UiStoryListResponse:
    raw = test_design_workspace_service.list_stories_for_ui(
        db,
        jira,
        project_key=project_key,
        status=status,
        q=q,
        limit=limit,
    )
    stories: list[UiStorySummary] = []
    for row in raw.get("items") or []:
        key = str(row.get("story_key") or "").strip().upper()
        if not key:
            continue
        active_run_id = row.get("active_workflow_run_id")
        stories.append(
            map_issue_to_ui_story(
                {
                    "issue_key": key,
                    "summary": row.get("title") or row.get("summary") or "",
                    "description": row.get("description") or "",
                    "status": row.get("status"),
                    "sprint": row.get("sprint"),
                    "assignee": row.get("assignee"),
                },
                settings=settings,
                active_run_id=str(active_run_id) if active_run_id else None,
            )
        )
    total = int(raw.get("total") if raw.get("total") is not None else len(stories))
    return UiStoryListResponse(stories=stories, total=total)
