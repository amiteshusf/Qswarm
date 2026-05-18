"""Branch / PR defaults for a repository connection."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.repository_connection import RepositoryConnection


class RepositoryBranchPolicy(Base):
    __tablename__ = "repository_branch_policies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repository_connections.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    base_branch_default: Mapped[str] = mapped_column(String(256), default="main", nullable=False)
    branch_naming_pattern: Mapped[str] = mapped_column(
        String(512), default="qswarm/{session_id}", nullable=False
    )
    allow_session_override: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    commit_message_template: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pr_title_template: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pr_body_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_reviewers_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    default_labels_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    repository_connection: Mapped["RepositoryConnection"] = relationship(
        "RepositoryConnection", back_populates="branch_policy"
    )
