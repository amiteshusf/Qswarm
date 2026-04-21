"""Abstract publisher for internal test design drafts."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.schemas.test_design_publish import PublishResult, TestDesignPublishPackage


class TestDesignPublisher(ABC):
    """Pluggable adapter: internal ``TestDesignPublishPackage`` → external system."""

    @abstractmethod
    def publish(
        self,
        package: TestDesignPublishPackage,
        *,
        db: Session,
        workflow_run_id: uuid.UUID,
        reviewer_account_id: str | None = None,
    ) -> PublishResult:
        """Persist side effects and return a structured outcome."""
