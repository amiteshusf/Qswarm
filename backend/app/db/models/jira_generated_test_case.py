"""Persisted Jira draft test cases produced from Sprint 1 test design publish."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class JiraGeneratedTestCase(Base):
    __tablename__ = "jira_generated_test_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_jira_issue_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generated_jira_issue_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewer_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_system: Mapped[str] = mapped_column(String(32), default="jira", nullable=False)
    publish_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    link_status: Mapped[str] = mapped_column(String(32), nullable=False, default="skipped")
    assignment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_attempted")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    internal_sync_version: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    jira_sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workflow_run: Mapped["WorkflowRun"] = relationship(
        "WorkflowRun", back_populates="jira_generated_test_cases"
    )
