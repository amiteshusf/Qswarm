"""Publish Sprint 1 test design as a single linked Jira review Task (comment-driven review)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError, plain_lines_to_adf, project_key_from_issue_key
from app.core.config import Settings
from app.core.constants import ActorType, AuditEventType
from app.db.models.jira_test_design_review_issue import JiraTestDesignReviewIssue
from app.publishers.base import TestDesignPublisher
from app.schemas.test_design_publish import PublishResult, TestDesignPublishPackage
from app.services import audit_service
from app.services.jira_review_description_builder import build_review_issue_description_lines


class JiraTestDesignPublisher(TestDesignPublisher):
    """Creates one Jira Task linked to the parent story for human review (not multiple draft cases)."""

    def __init__(self, jira: JiraClient, settings: Settings):
        self._jira = jira
        self._settings = settings

    def publish(
        self,
        package: TestDesignPublishPackage,
        *,
        db: Session,
        workflow_run_id: uuid.UUID,
        reviewer_account_id: str | None = None,
    ) -> PublishResult:
        actor = "jira_test_design_publisher"
        design = package.full_design_json if isinstance(package.full_design_json, dict) else {}
        audit_service.write_audit(
            db,
            event_type=AuditEventType.TEST_DESIGN_PUBLISH_STARTED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor,
            workflow_run_id=workflow_run_id,
            step_name="publish_test_design",
            entity_type="workflow_run",
            entity_id=str(workflow_run_id),
            payload={
                "target": "jira",
                "mode": "single_review_issue",
                "parent_issue_key": package.parent_issue_key,
            },
        )
        db.flush()

        if not package.cases:
            audit_service.write_audit(
                db,
                event_type=AuditEventType.TEST_DESIGN_PUBLISH_COMPLETED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=actor,
                workflow_run_id=workflow_run_id,
                step_name="publish_test_design",
                entity_type="workflow_run",
                entity_id=str(workflow_run_id),
                payload={"target": "jira", "skipped": True, "reason": "no_cases"},
            )
            db.flush()
            return PublishResult(success=True, hard_failure=False, warnings=["no_draft_cases_to_publish"])

        try:
            project_key = project_key_from_issue_key(package.parent_issue_key)
        except JiraClientError as e:
            audit_service.write_audit(
                db,
                event_type=AuditEventType.TEST_DESIGN_PUBLISH_FAILED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=actor,
                workflow_run_id=workflow_run_id,
                step_name="publish_test_design",
                entity_type="workflow_run",
                entity_id=str(workflow_run_id),
                payload={"error": str(e)},
            )
            db.flush()
            return PublishResult(success=False, hard_failure=True, errors=[str(e)])

        parent_key = package.parent_issue_key.strip().upper()
        summary = f"Draft Test Design: {parent_key}"[:254]
        desc_lines = build_review_issue_description_lines(parent_issue_key=parent_key, design=design)
        desc_adf = plain_lines_to_adf(desc_lines)
        warnings: list[str] = []
        reviewer = (reviewer_account_id or self._settings.jira_default_test_reviewer_account_id or "").strip() or None

        try:
            resp = self._jira.create_issue(
                project_key=project_key,
                summary=summary,
                description_adf=desc_adf,
                issue_type_name="Task",
                labels=["qswarm-draft-test-design-review"],
            )
            review_key = str(resp.get("key") or "").strip().upper()
            if not review_key:
                raise JiraClientError("Jira create_issue returned empty key", status_code=500)
        except JiraClientError as e:
            err = str(e)[:2000]
            row = JiraTestDesignReviewIssue(
                workflow_run_id=workflow_run_id,
                parent_jira_issue_key=parent_key,
                review_jira_issue_key=None,
                artifact_id=package.source_artifact_id,
                publish_status="failed",
                last_sync_error=err,
            )
            db.add(row)
            db.flush()
            audit_service.write_audit(
                db,
                event_type=AuditEventType.TEST_DESIGN_PUBLISH_FAILED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=actor,
                workflow_run_id=workflow_run_id,
                step_name="publish_test_design",
                entity_type="jira_test_design_review_issue",
                entity_id=str(row.id),
                payload={"error": err[:500]},
            )
            db.flush()
            return PublishResult(success=False, hard_failure=True, errors=[err], warnings=warnings)

        rev_row = JiraTestDesignReviewIssue(
            workflow_run_id=workflow_run_id,
            parent_jira_issue_key=parent_key,
            review_jira_issue_key=review_key,
            artifact_id=package.source_artifact_id,
            publish_status="published",
            last_sync_error=None,
        )
        db.add(rev_row)
        db.flush()

        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_REVIEW_ISSUE_CREATED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor,
            workflow_run_id=workflow_run_id,
            step_name="publish_test_design",
            entity_type="jira_test_design_review_issue",
            entity_id=str(rev_row.id),
            payload={"review_jira_issue_key": review_key, "parent": parent_key},
        )
        db.flush()

        try:
            self._jira.link_issues(
                inward_issue_key=review_key,
                outward_issue_key=parent_key,
                link_type_name="Relates",
            )
        except JiraClientError as e:
            msg = str(e)[:800]
            warnings.append(f"link_failed:{review_key}:{msg}")

        if reviewer:
            try:
                self._jira.assign_issue(review_key, reviewer)
            except JiraClientError as e:
                msg = str(e)[:800]
                warnings.append(f"assign_failed:{review_key}:{msg}")

        summary_lines = [
            "QSwarm Sprint 1 published a single draft test design review Task.",
            f"Review issue: {review_key}",
        ]
        try:
            self._jira.add_comment(parent_key, plain_lines_to_adf(summary_lines))
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PARENT_COMMENT_ADDED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=actor,
                workflow_run_id=workflow_run_id,
                step_name="publish_test_design",
                entity_type="jira_issue",
                entity_id=parent_key,
                payload={"review_jira_issue_key": review_key},
            )
            db.flush()
        except JiraClientError as e:
            msg = str(e)[:800]
            warnings.append(f"parent_comment_failed:{msg}")
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PARENT_COMMENT_FAILED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=actor,
                workflow_run_id=workflow_run_id,
                step_name="publish_test_design",
                entity_type="jira_issue",
                entity_id=parent_key,
                payload={"error": msg[:500]},
            )
            db.flush()

        audit_service.write_audit(
            db,
            event_type=AuditEventType.TEST_DESIGN_PUBLISH_COMPLETED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor,
            workflow_run_id=workflow_run_id,
            step_name="publish_test_design",
            entity_type="workflow_run",
            entity_id=str(workflow_run_id),
            payload={"review_jira_issue_key": review_key, "warning_count": len(warnings)},
        )
        db.flush()

        return PublishResult(
            success=True,
            hard_failure=False,
            created_issue_keys=[review_key],
            warnings=warnings,
        )
