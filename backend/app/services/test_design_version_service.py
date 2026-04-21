"""Test design version rows: lineage and current pointer per workflow run."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models.agent_artifact import AgentArtifact
from app.db.models.test_design_version import TestDesignVersion


def record_initial_version(
    db: Session,
    *,
    workflow_run_id: uuid.UUID,
    artifact_id: uuid.UUID,
    created_by: str,
) -> TestDesignVersion:
    """Create version 1 for the first Sprint 1 test design artifact (idempotent if v1 exists)."""
    existing = db.scalar(
        select(TestDesignVersion).where(
            TestDesignVersion.workflow_run_id == workflow_run_id,
            TestDesignVersion.version_number == 1,
        )
    )
    if existing:
        return existing

    row = TestDesignVersion(
        workflow_run_id=workflow_run_id,
        artifact_id=artifact_id,
        version_number=1,
        parent_version_id=None,
        version_action="initial",
        source_feedback_id=None,
        is_current=True,
        created_by=created_by[:256],
        notes=None,
    )
    db.add(row)
    db.flush()
    return row


def get_current_version(db: Session, workflow_run_id: uuid.UUID) -> TestDesignVersion | None:
    return db.scalar(
        select(TestDesignVersion).where(
            TestDesignVersion.workflow_run_id == workflow_run_id,
            TestDesignVersion.is_current.is_(True),
        )
    )


def get_current_version_number(db: Session, workflow_run_id: uuid.UUID) -> int | None:
    v = get_current_version(db, workflow_run_id)
    return v.version_number if v else None


def list_versions(db: Session, workflow_run_id: uuid.UUID) -> list[TestDesignVersion]:
    return list(
        db.scalars(
            select(TestDesignVersion)
            .where(TestDesignVersion.workflow_run_id == workflow_run_id)
            .order_by(TestDesignVersion.version_number.asc())
        ).all()
    )


def mark_all_versions_not_current(db: Session, workflow_run_id: uuid.UUID) -> None:
    db.execute(
        update(TestDesignVersion)
        .where(TestDesignVersion.workflow_run_id == workflow_run_id)
        .values(is_current=False)
    )
    db.flush()


def create_new_version(
    db: Session,
    *,
    workflow_run_id: uuid.UUID,
    artifact_id: uuid.UUID,
    version_number: int,
    parent_version_id: uuid.UUID | None,
    version_action: str,
    source_feedback_id: uuid.UUID | None,
    created_by: str,
    notes: str | None = None,
) -> TestDesignVersion:
    mark_all_versions_not_current(db, workflow_run_id)
    row = TestDesignVersion(
        workflow_run_id=workflow_run_id,
        artifact_id=artifact_id,
        version_number=version_number,
        parent_version_id=parent_version_id,
        version_action=version_action,
        source_feedback_id=source_feedback_id,
        is_current=True,
        created_by=created_by[:256],
        notes=notes,
    )
    db.add(row)
    db.flush()
    return row


def get_artifact_for_version(db: Session, version: TestDesignVersion) -> AgentArtifact | None:
    return db.get(AgentArtifact, version.artifact_id)


def compute_next_version_number(db: Session, workflow_run_id: uuid.UUID) -> int:
    mx = db.scalar(
        select(func.coalesce(func.max(TestDesignVersion.version_number), 0)).where(
            TestDesignVersion.workflow_run_id == workflow_run_id
        )
    )
    return int(mx or 0) + 1
