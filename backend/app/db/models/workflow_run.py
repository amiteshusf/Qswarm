"""Workflow execution record."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import WorkflowRunStatus
from app.db.base import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    jira_story_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jira_stories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=WorkflowRunStatus.PENDING.value, nullable=False, index=True
    )
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    graph_state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    initiated_by: Mapped[str] = mapped_column(String(256), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    jira_story: Mapped["JiraStory | None"] = relationship("JiraStory", back_populates="workflow_runs")
    artifacts: Mapped[list["AgentArtifact"]] = relationship(
        "AgentArtifact", back_populates="workflow_run", cascade="all, delete-orphan"
    )
    approvals: Mapped[list["Approval"]] = relationship(
        "Approval", back_populates="workflow_run", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="workflow_run"
    )
    automation_jobs: Mapped[list["AutomationJob"]] = relationship(
        "AutomationJob", back_populates="workflow_run"
    )
