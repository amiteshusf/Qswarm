"""Update existing Jira draft Tasks when internal test design changes (Sprint 1 review)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError, plain_lines_to_adf, project_key_from_issue_key
from app.core.config import Settings
from app.core.constants import ActorType, AuditEventType
from app.db.models.jira_generated_test_case import JiraGeneratedTestCase
from app.publishers.jira_publisher import _jira_description_lines
from app.schemas.test_design_publish import TestDesignPublishPackage
from app.services import audit_service


def _ordered_published_rows(db: Session, workflow_run_id: uuid.UUID) -> list[JiraGeneratedTestCase]:
    return list(
        db.scalars(
            select(JiraGeneratedTestCase)
            .where(
                JiraGeneratedTestCase.workflow_run_id == workflow_run_id,
                JiraGeneratedTestCase.publish_status == "published",
                JiraGeneratedTestCase.generated_jira_issue_key.isnot(None),
            )
            .order_by(JiraGeneratedTestCase.case_index.asc(), JiraGeneratedTestCase.created_at.asc())
        ).all()
    )


def sync_package_to_jira_draft_rows(
    db: Session,
    jira: JiraClient,
    settings: Settings,
    *,
    workflow_run_id: uuid.UUID,
    package: TestDesignPublishPackage,
    internal_sync_version: int,
) -> None:
    """
    Map ``package.cases`` by index to existing ``jira_generated_test_cases`` rows and
    ``update_issue`` in place. Create new Jira Tasks for extra cases. Comment on surplus rows
    when the new package is smaller than the previous mapping.
    """
    rows = _ordered_published_rows(db, workflow_run_id)
    if not package.cases:
        raise JiraClientError("No draft cases in package to sync", status_code=400)
    parent_key = package.parent_issue_key.strip().upper()
    project_key = project_key_from_issue_key(parent_key)
    reviewer = (settings.jira_default_test_reviewer_account_id or "").strip() or None
    actor = "jira_draft_test_case_sync"

    for idx, draft in enumerate(package.cases):
        desc_adf = plain_lines_to_adf(_jira_description_lines(draft))
        summary = f"Test Case: {draft.title}"[:254]
        if idx < len(rows):
            row = rows[idx]
            key = (row.generated_jira_issue_key or "").strip().upper()
            if not key:
                continue
            jira.update_issue(key, summary=summary, description_adf=desc_adf)
            row.artifact_id = package.source_artifact_id
            row.title = draft.title[:500]
            row.case_type = draft.case_type
            row.case_index = idx
            row.internal_sync_version = internal_sync_version
            row.jira_sync_status = "updated"
            row.last_sync_error = None
            db.flush()
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_DRAFT_TEST_CASES_UPDATED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=actor,
                workflow_run_id=workflow_run_id,
                step_name="sync_jira_draft_test_cases",
                entity_type="jira_generated_test_case",
                entity_id=str(row.id),
                payload={"generated_jira_issue_key": key, "case_index": idx},
            )
            db.flush()
            continue

        resp = jira.create_issue(
            project_key=project_key,
            summary=summary,
            description_adf=desc_adf,
            issue_type_name="Task",
            labels=["qswarm-draft-test-case"],
        )
        child_key = str(resp.get("key") or "").strip().upper()
        if not child_key:
            raise JiraClientError("Jira create_issue returned empty key", status_code=500)
        new_row = JiraGeneratedTestCase(
            workflow_run_id=workflow_run_id,
            parent_jira_issue_key=parent_key,
            generated_jira_issue_key=child_key,
            artifact_id=package.source_artifact_id,
            title=draft.title[:500],
            case_type=draft.case_type,
            reviewer_account_id=reviewer,
            external_system="jira",
            publish_status="published",
            link_status="skipped",
            assignment_status="not_attempted",
            error_detail=None,
            case_index=idx,
            internal_sync_version=internal_sync_version,
            jira_sync_status="published",
            last_sync_error=None,
        )
        db.add(new_row)
        db.flush()
        try:
            jira.link_issues(inward_issue_key=child_key, outward_issue_key=parent_key, link_type_name="Relates")
            new_row.link_status = "linked"
        except JiraClientError:
            new_row.link_status = "failed"
        if reviewer:
            try:
                jira.assign_issue(child_key, reviewer)
                new_row.assignment_status = "assigned"
            except JiraClientError:
                new_row.assignment_status = "failed"
        else:
            new_row.assignment_status = "skipped"
        db.flush()
        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_TEST_CASE_CREATED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor,
            workflow_run_id=workflow_run_id,
            step_name="sync_jira_draft_test_cases",
            entity_type="jira_generated_test_case",
            entity_id=str(new_row.id),
            payload={"generated_jira_issue_key": child_key, "case_index": idx, "reason": "expanded_set"},
        )
        db.flush()

    if len(rows) > len(package.cases):
        note = (
            f"QSwarm: internal draft shrank to {len(package.cases)} case(s) for sync version "
            f"{internal_sync_version}. This Task is outside the active mapped slice."
        )
        for surplus in rows[len(package.cases) :]:
            sk = (surplus.generated_jira_issue_key or "").strip().upper()
            if not sk:
                continue
            try:
                jira.add_comment(sk, plain_lines_to_adf([note]))
            except JiraClientError:
                pass
            surplus.jira_sync_status = "superseded_slice"
            surplus.internal_sync_version = internal_sync_version
            db.flush()
