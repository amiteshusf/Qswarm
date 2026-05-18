"""Human review / control action on an automation session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_session import AutomationSession
    from app.db.models.automation_revision_round import AutomationRevisionRound


class AutomationReviewRequest(Base):
    __tablename__ = "automation_review_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_round_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("automation_revision_rounds.id", ondelete="SET NULL"), nullable=True
    )
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    instruction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_scope: Mapped[str | None] = mapped_column(String(512), nullable=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="recorded", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["AutomationSession"] = relationship("AutomationSession", back_populates="review_requests")
    revision_round: Mapped["AutomationRevisionRound | None"] = relationship(
        "AutomationRevisionRound", back_populates="review_requests"
    )
