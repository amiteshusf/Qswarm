"""Jira story snapshot."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class JiraStory(Base):
    __tablename__ = "jira_stories"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    issue_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    issue_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assignee: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reporter: Mapped[str | None] = mapped_column(String(256), nullable=True)
    labels_json: Mapped[list | dict | None] = mapped_column(JSON, nullable=True)
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workflow_runs: Mapped[list["WorkflowRun"]] = relationship(
        "WorkflowRun", back_populates="jira_story"
    )
