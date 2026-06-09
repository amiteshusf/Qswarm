"""Short-lived workspace cache for hosted automation sessions (create-pr durability)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_session import AutomationSession


class WorkspaceCacheEntry(Base):
    __tablename__ = "workspace_cache_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    automation_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("automation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repository_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    workspace_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped["AutomationSession"] = relationship("AutomationSession", back_populates="workspace_cache_entries")
