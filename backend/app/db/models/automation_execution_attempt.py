"""Recorded Playwright (or future) execution attempt for a session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_session import AutomationSession
    from app.db.models.automation_revision_round import AutomationRevisionRound


class AutomationExecutionAttempt(Base):
    __tablename__ = "automation_execution_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_round_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_revision_rounds.id", ondelete="SET NULL"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    target_test_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    command_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["AutomationSession"] = relationship(
        "AutomationSession", back_populates="execution_attempts"
    )
    revision_round: Mapped["AutomationRevisionRound | None"] = relationship(
        "AutomationRevisionRound", back_populates="execution_attempts"
    )
