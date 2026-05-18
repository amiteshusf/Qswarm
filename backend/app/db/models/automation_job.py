"""Internal automation execution job (Sprint 2+ source of truth for automation state)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import AutomationJobStatus
from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_job_review_action import AutomationJobReviewAction
    from app.db.models.automation_session import AutomationSession
    from app.db.models.pr_record import PrRecord


class AutomationJob(Base):
    __tablename__ = "automation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    approved_case_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repo_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repo_owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repo_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repo_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_branch: Mapped[str] = mapped_column(String(256), default="main", nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(64), default=AutomationJobStatus.PENDING.value, nullable=False, index=True
    )
    framework_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_spec_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    repo_context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    change_plan_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_patch_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    repair_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_attempt_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    workflow_run: Mapped["WorkflowRun | None"] = relationship(
        "WorkflowRun", back_populates="automation_jobs"
    )
    review_actions: Mapped[list["AutomationJobReviewAction"]] = relationship(
        "AutomationJobReviewAction",
        back_populates="automation_job",
        cascade="all, delete-orphan",
    )
    pr_records: Mapped[list["PrRecord"]] = relationship(
        "PrRecord",
        back_populates="automation_job",
        cascade="all, delete-orphan",
    )
    automation_session: Mapped["AutomationSession | None"] = relationship(
        "AutomationSession",
        back_populates="automation_job",
        uselist=False,
    )
