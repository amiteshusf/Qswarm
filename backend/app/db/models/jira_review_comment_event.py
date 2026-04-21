"""One persisted row per Jira review comment processed (or intentionally skipped)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class JiraReviewCommentEvent(Base):
    __tablename__ = "jira_review_comment_events"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "jira_comment_id", name="uq_jira_review_comment_run_comment"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    review_issue_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    jira_comment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_action_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("test_design_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    processed_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    response_comment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_feedback_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("test_design_feedback.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workflow_run: Mapped["WorkflowRun"] = relationship(
        "WorkflowRun", back_populates="jira_review_comment_events"
    )
