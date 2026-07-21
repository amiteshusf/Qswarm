"""Top-level Sprint 2 automation control-plane session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import AutomationSessionStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_job import AutomationJob
    from app.db.models.automation_execution_attempt import AutomationExecutionAttempt
    from app.db.models.repository_connection import RepositoryConnection
    from app.db.models.automation_patch_version import AutomationPatchVersion
    from app.db.models.automation_plan_version import AutomationPlanVersion
    from app.db.models.automation_revision_round import AutomationRevisionRound
    from app.db.models.automation_review_request import AutomationReviewRequest
    from app.db.models.code_review_request import CodeReviewRequest
    from app.db.models.workflow_run import WorkflowRun
    from app.db.models.workspace_cache_entry import WorkspaceCacheEntry


class AutomationSession(Base):
    __tablename__ = "automation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    automation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_jobs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    repo_owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repo_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    repository_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_branch: Mapped[str] = mapped_column(String(256), default="main", nullable=False)
    coding_engine: Mapped[str] = mapped_column(String(64), default="stub", nullable=False)
    status: Mapped[str] = mapped_column(
        String(64), default=AutomationSessionStatus.PENDING.value, nullable=False, index=True
    )
    current_round_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approved_case_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    plan_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    repository_connection: Mapped["RepositoryConnection | None"] = relationship(
        "RepositoryConnection",
        foreign_keys=[repository_connection_id],
    )
    automation_job: Mapped["AutomationJob | None"] = relationship(
        "AutomationJob", back_populates="automation_session", foreign_keys=[automation_job_id]
    )
    workflow_run: Mapped["WorkflowRun | None"] = relationship(
        "WorkflowRun", back_populates="automation_sessions"
    )
    revision_rounds: Mapped[list["AutomationRevisionRound"]] = relationship(
        "AutomationRevisionRound",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AutomationRevisionRound.round_number",
    )
    plan_versions: Mapped[list["AutomationPlanVersion"]] = relationship(
        "AutomationPlanVersion",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    patch_versions: Mapped[list["AutomationPatchVersion"]] = relationship(
        "AutomationPatchVersion",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    execution_attempts: Mapped[list["AutomationExecutionAttempt"]] = relationship(
        "AutomationExecutionAttempt",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    review_requests: Mapped[list["AutomationReviewRequest"]] = relationship(
        "AutomationReviewRequest",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    code_review_requests: Mapped[list["CodeReviewRequest"]] = relationship(
        "CodeReviewRequest",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    workspace_cache_entries: Mapped[list["WorkspaceCacheEntry"]] = relationship(
        "WorkspaceCacheEntry",
        back_populates="session",
        cascade="all, delete-orphan",
    )
