"""Preflight validation and duplicate-run checks for Jira → Sprint 1 pickup."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import WorkflowRunStatus
from app.db.models.workflow_run import WorkflowRun
from app.schemas.jira_pickup import PICKUP_LABEL_DEFAULT

SUPPORTED_ISSUE_TYPES = frozenset({"story", "task"})

# Workflow runs in these states block a new pickup for the same Jira key.
_ACTIVE_STATUSES = frozenset(
    {
        WorkflowRunStatus.PENDING.value,
        WorkflowRunStatus.RUNNING.value,
        WorkflowRunStatus.AWAITING_APPROVAL.value,
        WorkflowRunStatus.APPROVED.value,
    }
)

def pickup_label() -> str:
    return PICKUP_LABEL_DEFAULT


def jira_pickup_jql(label: str | None = None) -> str:
    """JQL used by the polling job (label + Story/Task only)."""
    lab = (label or PICKUP_LABEL_DEFAULT).strip()
    # Jira JQL: quoted label, issuetype names as configured on the site.
    return (
        f'labels = "{lab}" AND issuetype in (Story, Task) '
        f"ORDER BY updated DESC"
    )


def _issue_key_from_graph(gs: Any) -> str | None:
    if not isinstance(gs, dict):
        return None
    raw = gs.get("jira_issue_key")
    if raw is None:
        return None
    return str(raw).strip().upper() or None


def has_active_workflow_for_jira_issue(db: Session, issue_key: str) -> bool:
    """True if any non-terminal workflow run exists for this Jira issue key."""
    want = issue_key.strip().upper()
    stmt = select(WorkflowRun.graph_state_json, WorkflowRun.status).where(
        WorkflowRun.status.in_(tuple(_ACTIVE_STATUSES))
    )
    for row in db.execute(stmt):
        gs, st = row[0], row[1]
        if st not in _ACTIVE_STATUSES:
            continue
        if _issue_key_from_graph(gs) == want:
            return True
    return False


def _norm_type(name: str | None) -> str:
    return (name or "").strip().lower()


def _too_vague_summary(summary: str) -> bool:
    """Conservative heuristic: very short or only tiny generic tokens."""
    s = (summary or "").strip()
    if len(s) < 8:
        return True
    words = [w for w in re.split(r"\s+", s) if w]
    if len(words) > 2:
        return False
    generic = {
        "story",
        "task",
        "bug",
        "fix",
        "test",
        "todo",
        "update",
        "wip",
        "draft",
        "change",
        "work",
        "item",
        "new",
    }
    if not words:
        return True
    if len(words) <= 2 and all(re.sub(r"[^\w]", "", w).lower() in generic for w in words):
        return True
    return False


def evaluate_pickup_candidate(
    *,
    issue_key: str,
    labels: list[str],
    issue_type: str | None,
    status_category_key: str | None,
    summary: str | None,
    db: Session,
) -> tuple[bool, str | None]:
    """
    Apply hard/soft pickup rules. Returns (eligible, skip_reason or None).

    skip_reason matches API contract (snake_case strings).
    """
    key_upper = issue_key.strip().upper()
    label = pickup_label()
    labels_norm = [str(x) for x in (labels or [])]
    if label not in labels_norm:
        return False, "missing_label"

    it = _norm_type(issue_type)
    if it not in SUPPORTED_ISSUE_TYPES:
        return False, "unsupported_issue_type"

    if (status_category_key or "").lower() == "done":
        return False, "done_status_category"

    summ = (summary or "").strip()
    if not summ:
        return False, "missing_summary"

    if has_active_workflow_for_jira_issue(db, key_upper):
        return False, "duplicate_active_run"

    if _too_vague_summary(summ):
        return False, "too_vague"

    return True, None
