"""Story intake orchestration (agent + optional persistence)."""

from __future__ import annotations

from typing import Any

from app.agents.story_intake_agent import run_intake
from app.connectors.jira_client import JiraClient


def build_intake_from_client(client: JiraClient, issue_key: str) -> dict[str, Any]:
    """Fetch issue via client and return intake artifact dict."""
    data = client.get_issue(issue_key)
    fields = {
        "issue_key": data["issue_key"],
        "summary": data.get("summary") or "",
        "description": data.get("description") or "",
        "labels": data.get("labels") or [],
        "priority": data.get("priority") or "",
        "issue_type": data.get("issue_type") or "",
        "status": data.get("status") or "",
    }
    return run_intake(fields)
