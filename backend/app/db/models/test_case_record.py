"""Approved test case registry — bridge between Sprint 1 design and Sprint 2 automation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.agent_artifact import AgentArtifact
    from app.db.models.automation_session import AutomationSession
    from app.db.models.test_design_version import TestDesignVersion
    from app.db.models.workflow_run import WorkflowRun


class TestCaseRecord(Base):
    __tablename__ = "test_case_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registry_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_story_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(32), default="jira", nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    external_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_type: Mapped[str] = mapped_column(String(32), default="generic", nullable=False)
    case_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    steps_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    expected_results_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    preconditions_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    assumptions_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    missing_information_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    publication_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    automation_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    automation_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("automation_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    test_design_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("test_design_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provenance_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workflow_run: Mapped["WorkflowRun"] = relationship("WorkflowRun", back_populates="test_case_records")
    automation_session: Mapped["AutomationSession | None"] = relationship(
        "AutomationSession", foreign_keys=[automation_session_id]
    )
    source_artifact: Mapped["AgentArtifact | None"] = relationship("AgentArtifact")
    test_design_version: Mapped["TestDesignVersion | None"] = relationship("TestDesignVersion")
