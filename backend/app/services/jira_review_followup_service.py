"""Post delta-only comments on the linked Sprint 1 Jira review issue."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError, plain_lines_to_adf
from app.db.models.jira_test_design_review_issue import JiraTestDesignReviewIssue


def post_delta_lines_on_review_issue(
    db: Session,
    jira: JiraClient,
    *,
    workflow_run_id: uuid.UUID,
    lines: list[str],
) -> str | None:
    """Append a QSwarm reply comment on the review issue. Returns comment id or None."""
    row = db.scalar(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == workflow_run_id)
    )
    if row is None or not row.review_jira_issue_key:
        return None
    key = row.review_jira_issue_key.strip().upper()
    if not lines:
        return None
    return jira.add_comment(key, plain_lines_to_adf(lines))
