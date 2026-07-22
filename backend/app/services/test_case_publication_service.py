"""Publish approved test case records to external systems (Jira first)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, plain_lines_to_adf, project_key_from_issue_key
from app.core.config import get_settings
from app.core.constants import (
    ActorType,
    AuditEventType,
    TestCaseAutomationStatus,
    TestCasePublicationStatus,
)
from app.db.models.test_case_record import TestCaseRecord
from app.services import audit_service


def _build_jira_description_lines(record: TestCaseRecord) -> list[str]:
    lines = [
        f"*QSwarm approved test case* — registry `{record.registry_key}`",
        "",
        f"*Objective:* {record.objective or record.title}",
    ]
    if record.preconditions_json:
        lines.extend(["", "*Preconditions:*", *[f"- {x}" for x in record.preconditions_json[:15]]])
    if record.steps_json:
        lines.extend(["", "*Steps:*", *[f"#. {x}" for x in record.steps_json[:25]]])
    if record.expected_results_json:
        lines.extend(["", "*Expected results:*", *[f"- {x}" for x in record.expected_results_json[:15]]])
    if record.missing_information_json:
        lines.extend(["", "*Missing information:*", *[f"- {x}" for x in record.missing_information_json[:10]]])
    lines.extend(["", f"*Source story:* {record.source_story_key}"])
    return lines


def publish_test_case_record(db: Session, record_id: uuid.UUID, *, actor_id: str) -> TestCaseRecord:
    """Publish one registry row to Jira (or stub) and mark automation-ready."""
    record = db.get(TestCaseRecord, record_id)
    if record is None:
        raise ValueError("test_case_not_found")
    if record.publication_status == TestCasePublicationStatus.PUBLISHED.value:
        return record

    settings = get_settings()
    jira = JiraClient(settings)
    project_key = project_key_from_issue_key(record.source_story_key)
    summary = f"Test Case: {record.title}"[:254]
    description_adf = plain_lines_to_adf(_build_jira_description_lines(record))

    audit_service.write_audit(
        db,
        event_type=AuditEventType.TEST_CASE_PUBLISHED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id[:256],
        workflow_run_id=record.workflow_run_id,
        step_name="publish_test_case",
        entity_type="test_case_record",
        entity_id=str(record.id),
        payload={"source_system": record.source_system, "registry_key": record.registry_key},
    )
    db.flush()

    try:
        created = jira.create_issue(
            project_key=project_key,
            summary=summary,
            description_adf=description_adf,
            issue_type_name="Task",
            labels=["qswarm-approved-test-case"],
        )
        child_key = str(created.get("key") or "").strip().upper()
        if not child_key:
            raise ValueError("jira_create_missing_key")
        try:
            jira.link_issues(
                inward_issue_key=child_key,
                outward_issue_key=record.source_story_key,
                link_type_name="Relates",
            )
        except Exception:
            pass

        base = (settings.jira_base_url or "").rstrip("/")
        record.external_id = child_key
        record.external_url = f"{base}/browse/{child_key}" if base else None
        record.publication_status = TestCasePublicationStatus.PUBLISHED.value
        record.publication_error = None
        record.published_at = datetime.now(timezone.utc)
        record.automation_status = TestCaseAutomationStatus.AUTOMATION_READY.value
    except Exception as e:
        record.publication_status = TestCasePublicationStatus.FAILED.value
        record.publication_error = str(e)[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.TEST_CASE_PUBLISH_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id="test_case_publication_service",
            workflow_run_id=record.workflow_run_id,
            step_name="publish_test_case",
            entity_type="test_case_record",
            entity_id=str(record.id),
            payload={"error": str(e)[:500]},
        )
        db.flush()
        raise

    db.flush()
    return record
