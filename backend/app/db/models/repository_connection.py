"""Reusable repository + provider configuration for PR / MR creation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.repository_branch_policy import RepositoryBranchPolicy


class RepositoryConnection(Base):
    __tablename__ = "repository_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_or_org: Mapped[str] = mapped_column(String(256), nullable=False)
    project_or_workspace: Mapped[str | None] = mapped_column(String(256), nullable=True)
    repo_name: Mapped[str] = mapped_column(String(256), nullable=False)
    clone_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    default_branch: Mapped[str] = mapped_column(String(256), default="main", nullable=False)
    auth_type: Mapped[str] = mapped_column(String(64), default="github_pat_env", nullable=False)
    credential_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    branch_policy: Mapped["RepositoryBranchPolicy | None"] = relationship(
        "RepositoryBranchPolicy",
        back_populates="repository_connection",
        uselist=False,
        cascade="all, delete-orphan",
    )
