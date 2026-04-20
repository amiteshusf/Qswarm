"""Jira fetch and persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError
from app.core.constants import ActorType, AuditEventType
from app.db.models.jira_story import JiraStory
from app.services import audit_service


def normalized_to_story_row(data: dict[str, Any]) -> dict[str, Any]:
    """Map connector output to JiraStory constructor kwargs (excluding id)."""
    return {
        "issue_key": data["issue_key"],
        "issue_id": data.get("issue_id"),
        "summary": data.get("summary") or "",
        "description": data.get("description"),
        "issue_type": data.get("issue_type"),
        "priority": data.get("priority"),
        "status": data.get("status"),
        "assignee": data.get("assignee"),
        "reporter": data.get("reporter"),
        "labels_json": data.get("labels") or [],
        "raw_payload_json": data.get("raw_payload") or {},
        "fetched_at": datetime.now(timezone.utc),
    }


def fetch_and_upsert_story(
    db: Session,
    client: JiraClient,
    issue_key: str,
    *,
    workflow_run_id: uuid.UUID | None = None,
    actor_id: str = "system",
) -> JiraStory:
    """Fetch from Jira (or stub), upsert `JiraStory`, audit."""
    try:
        data = client.get_issue(issue_key)
    except JiraClientError:
        raise

    kwargs = normalized_to_story_row(data)
    existing = db.execute(
        select(JiraStory).where(JiraStory.issue_key == kwargs["issue_key"])
    ).scalar_one_or_none()

    if existing:
        for k, v in kwargs.items():
            setattr(existing, k, v)
        story = existing
    else:
        story = JiraStory(**kwargs)
        db.add(story)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.JIRA_STORY_FETCHED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=actor_id,
        workflow_run_id=workflow_run_id,
        step_name="fetch_story",
        entity_type="jira_story",
        entity_id=str(story.id),
        payload={"issue_key": story.issue_key},
    )
    return story


def story_to_api_dict(story: JiraStory) -> dict[str, Any]:
    labels = story.labels_json
    if isinstance(labels, list):
        label_list = [str(x) for x in labels]
    else:
        label_list = []
    return {
        "issue_key": story.issue_key,
        "issue_id": story.issue_id,
        "summary": story.summary,
        "description": story.description,
        "issue_type": story.issue_type,
        "priority": story.priority,
        "status": story.status,
        "assignee": story.assignee,
        "reporter": story.reporter,
        "labels": label_list,
        "raw_available": story.raw_payload_json is not None,
    }
