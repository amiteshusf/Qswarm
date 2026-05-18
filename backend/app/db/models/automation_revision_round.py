"""One revision / generation round within an automation session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_execution_attempt import AutomationExecutionAttempt
    from app.db.models.automation_session import AutomationSession
    from app.db.models.automation_plan_version import AutomationPlanVersion
    from app.db.models.automation_patch_version import AutomationPatchVersion
    from app.db.models.automation_review_request import AutomationReviewRequest


class AutomationRevisionRound(Base):
    __tablename__ = "automation_revision_rounds"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_by: Mapped[str] = mapped_column(String(256), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    instruction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_scope: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="in_progress", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped["AutomationSession"] = relationship("AutomationSession", back_populates="revision_rounds")
    plan_versions: Mapped[list["AutomationPlanVersion"]] = relationship(
        "AutomationPlanVersion", back_populates="revision_round", cascade="all, delete-orphan"
    )
    patch_versions: Mapped[list["AutomationPatchVersion"]] = relationship(
        "AutomationPatchVersion", back_populates="revision_round", cascade="all, delete-orphan"
    )
    review_requests: Mapped[list["AutomationReviewRequest"]] = relationship(
        "AutomationReviewRequest", back_populates="revision_round"
    )
    execution_attempts: Mapped[list["AutomationExecutionAttempt"]] = relationship(
        "AutomationExecutionAttempt", back_populates="revision_round"
    )
