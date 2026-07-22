"""Test case registry — Sprint 1 approval to Sprint 2 automation backlog."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation_engine.coding_engine_names import CodingEngineName
from app.core.constants import (
    ActorType,
    AuditEventType,
    TestCaseApprovalStatus,
    TestCaseAutomationStatus,
    TestCasePublicationStatus,
)
from app.db.models.agent_artifact import AgentArtifact
from app.db.models.approval import Approval
from app.db.models.automation_session import AutomationSession
from app.db.models.test_case_record import TestCaseRecord
from app.db.models.test_design_version import TestDesignVersion
from app.db.models.workflow_run import WorkflowRun
from app.schemas.automation_session import AutomationSessionCreateRequest
from app.schemas.test_case_registry import TestCaseAutomateRequest
from app.services import audit_service, automation_session_service
from app.services.test_design_publish_builder import draft_cases_from_test_design_json
from app.services.test_case_publication_service import publish_test_case_record


def _registry_key(story_key: str, case_index: int) -> str:
    return f"{story_key.strip().upper()}-TC-{case_index:02d}"


def _story_key_from_run(run: WorkflowRun) -> str:
    gs = run.graph_state_json if isinstance(run.graph_state_json, dict) else {}
    key = str(gs.get("jira_issue_key") or "").strip().upper()
    if key:
        return key
    if run.jira_story and run.jira_story.issue_key:
        return str(run.jira_story.issue_key).strip().upper()
    return f"RUN-{str(run.id)[:8].upper()}"


def _current_test_design_version(db: Session, workflow_run_id: uuid.UUID) -> TestDesignVersion | None:
    return db.scalar(
        select(TestDesignVersion)
        .where(
            TestDesignVersion.workflow_run_id == workflow_run_id,
            TestDesignVersion.is_current.is_(True),
        )
        .limit(1)
    )


def materialize_test_cases_from_approved_workflow(
    db: Session,
    approval: Approval,
    *,
    actor_id: str,
    auto_publish: bool = True,
) -> list[TestCaseRecord]:
    """
    Extract approved test-design scenarios into durable registry rows.

    Idempotent per workflow run + case index (skips existing registry_key).
    """
    run = db.get(WorkflowRun, approval.workflow_run_id)
    if run is None:
        raise ValueError("workflow_run_not_found")

    artifact = db.get(AgentArtifact, approval.artifact_id)
    if artifact is None or not isinstance(artifact.content_json, dict):
        raise ValueError("test_design_artifact_missing")

    story_key = _story_key_from_run(run)
    version = _current_test_design_version(db, run.id)
    drafts = draft_cases_from_test_design_json(artifact.content_json, max_cases=10)
    if not drafts:
        return []

    created: list[TestCaseRecord] = []
    for idx, draft in enumerate(drafts, start=1):
        rkey = _registry_key(story_key, idx)
        existing = db.scalar(select(TestCaseRecord).where(TestCaseRecord.registry_key == rkey).limit(1))
        if existing is not None:
            created.append(existing)
            continue

        record = TestCaseRecord(
            registry_key=rkey,
            workflow_run_id=run.id,
            source_story_key=story_key,
            source_system="jira",
            title=draft.title[:512],
            summary=(draft.objective or draft.title)[:8000],
            objective=(draft.objective or "")[:2000] or None,
            case_type=draft.case_type,
            case_index=idx,
            steps_json=draft.steps[:50] if draft.steps else None,
            expected_results_json=draft.expected_results[:50] if draft.expected_results else None,
            preconditions_json=draft.preconditions[:30] if draft.preconditions else None,
            assumptions_json=draft.assumptions[:20] if draft.assumptions else None,
            missing_information_json=draft.missing_information[:20] if draft.missing_information else None,
            approval_status=TestCaseApprovalStatus.APPROVED.value,
            publication_status=TestCasePublicationStatus.PENDING.value,
            automation_status=TestCaseAutomationStatus.NOT_STARTED.value,
            source_artifact_id=artifact.id,
            test_design_version_id=version.id if version else None,
            provenance_json={
                "approval_id": str(approval.id),
                "workflow_run_id": str(run.id),
                "source_artifact_id": str(artifact.id),
            },
            created_by=actor_id[:256],
        )
        db.add(record)
        db.flush()
        created.append(record)

    audit_service.write_audit(
        db,
        event_type=AuditEventType.TEST_CASE_REGISTRY_MATERIALIZED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id[:256],
        workflow_run_id=run.id,
        step_name="test_case_registry",
        entity_type="workflow_run",
        entity_id=str(run.id),
        payload={"count": len(created), "story_key": story_key},
    )
    db.flush()

    if auto_publish:
        for record in created:
            if record.publication_status != TestCasePublicationStatus.PUBLISHED.value:
                try:
                    publish_test_case_record(db, record.id, actor_id=actor_id)
                except Exception:
                    pass

    return created


def record_to_api_dict(record: TestCaseRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "registry_key": record.registry_key,
        "workflow_run_id": str(record.workflow_run_id),
        "source_story_key": record.source_story_key,
        "source_system": record.source_system,
        "external_id": record.external_id,
        "external_url": record.external_url,
        "title": record.title,
        "summary": record.summary,
        "objective": record.objective,
        "case_type": record.case_type,
        "case_index": record.case_index,
        "steps": record.steps_json or [],
        "expected_results": record.expected_results_json or [],
        "preconditions": record.preconditions_json or [],
        "approval_status": record.approval_status,
        "publication_status": record.publication_status,
        "publication_error": record.publication_error,
        "published_at": record.published_at.isoformat() if record.published_at else None,
        "automation_status": record.automation_status,
        "automation_session_id": str(record.automation_session_id) if record.automation_session_id else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def list_test_cases_for_api(
    db: Session,
    *,
    status: str | None = None,
    workflow_run_id: uuid.UUID | None = None,
    source_story_key: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    q = select(TestCaseRecord).order_by(TestCaseRecord.updated_at.desc()).limit(min(limit, 500))
    if workflow_run_id is not None:
        q = q.where(TestCaseRecord.workflow_run_id == workflow_run_id)
    if source_story_key:
        q = q.where(TestCaseRecord.source_story_key == source_story_key.strip().upper())
    if status == "automation_ready":
        q = q.where(
            TestCaseRecord.publication_status == TestCasePublicationStatus.PUBLISHED.value,
            TestCaseRecord.automation_status.in_(
                [
                    TestCaseAutomationStatus.NOT_STARTED.value,
                    TestCaseAutomationStatus.AUTOMATION_READY.value,
                ]
            ),
            TestCaseRecord.automation_session_id.is_(None),
        )
    elif status:
        q = q.where(TestCaseRecord.automation_status == status)

    rows = list(db.scalars(q).all())
    return [record_to_api_dict(r) for r in rows]


def get_test_case_for_api(db: Session, record_id: uuid.UUID) -> dict[str, Any] | None:
    record = db.get(TestCaseRecord, record_id)
    if record is None:
        return None
    return record_to_api_dict(record)


def create_automation_session_from_test_case(
    db: Session,
    record_id: uuid.UUID,
    body: TestCaseAutomateRequest,
) -> AutomationSession:
    record = db.get(TestCaseRecord, record_id)
    if record is None:
        raise ValueError("test_case_not_found")
    if record.publication_status != TestCasePublicationStatus.PUBLISHED.value:
        raise ValueError("test_case_not_published")
    if record.automation_session_id is not None:
        raise ValueError("test_case_already_automated")

    try:
        CodingEngineName.parse(body.coding_engine)
    except ValueError as e:
        raise ValueError(str(e)) from e

    approved_id = (record.external_id or record.registry_key).strip()
    if not approved_id:
        raise ValueError("test_case_missing_external_id")

    create_body = AutomationSessionCreateRequest(
        approved_case_id=approved_id,
        created_by=body.created_by.strip(),
        coding_engine=body.coding_engine.strip().lower(),
        source_system=record.source_system,
        source_reference=record.source_story_key,
        workflow_run_id=record.workflow_run_id,
        repository_connection_id=body.repository_connection_id,
        repo_path=body.repo_path,
        base_branch=body.base_branch or "main",
        case_title=record.title,
        case_description=record.summary or record.objective,
        preconditions=record.preconditions_json,
        steps=record.steps_json,
        expected_results=record.expected_results_json,
    )
    sess = automation_session_service.create_automation_session(db, create_body)
    sess.test_case_record_id = record.id
    record.automation_session_id = sess.id
    record.automation_status = TestCaseAutomationStatus.IN_PROGRESS.value

    audit_service.write_audit(
        db,
        event_type=AuditEventType.TEST_CASE_AUTOMATION_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=body.created_by.strip()[:256],
        workflow_run_id=record.workflow_run_id,
        step_name="test_case_automate",
        entity_type="test_case_record",
        entity_id=str(record.id),
        payload={
            "automation_session_id": str(sess.id),
            "registry_key": record.registry_key,
            "external_id": record.external_id,
        },
    )
    db.flush()
    return sess
