"""LangGraph state for Sprint 1 workflow."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class Sprint1State(TypedDict, total=False):
    """Graph state passed between nodes; merged by LangGraph."""

    run_id: str
    jira_issue_key: str
    initiated_by: str
    jira_story: dict[str, Any]
    normalized_story: dict[str, Any]
    intake_artifact_id: str
    test_design_artifact_id: str
    publish_warnings: Annotated[list[str], operator.add]
    approval_id: str
    approval_status: str
    errors: Annotated[list[str], operator.add]
    audit_context: dict[str, Any]
