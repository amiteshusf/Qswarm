"""Pull request metadata for automation jobs (GitHub-first)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.automation_job import AutomationJob


class PrRecord(Base):
    __tablename__ = "pr_records"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    automation_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("automation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    repo_owner: Mapped[str] = mapped_column(String(256), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(256), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(256), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refresh_notes_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    automation_job: Mapped["AutomationJob"] = relationship("AutomationJob", back_populates="pr_records")
