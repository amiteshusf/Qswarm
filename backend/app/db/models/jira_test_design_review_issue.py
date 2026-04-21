"""Single Jira Task used as the Sprint 1 draft test design review thread."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class JiraTestDesignReviewIssue(Base):
    __tablename__ = "jira_test_design_review_issues"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    parent_jira_issue_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    review_jira_issue_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    publish_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workflow_run: Mapped["WorkflowRun"] = relationship(
        "WorkflowRun", back_populates="jira_test_design_review_issue"
    )
