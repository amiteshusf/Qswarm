"""Explicit processing of @QSwarm comments on the linked Jira draft review issue."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError, plain_lines_to_adf
from app.core.constants import ActorType, ApprovalStatus, AuditEventType, WorkflowRunStatus
from app.db.models.agent_artifact import AgentArtifact
from app.db.models.approval import Approval
from app.db.models.jira_review_comment_event import JiraReviewCommentEvent
from app.db.models.jira_test_design_review_issue import JiraTestDesignReviewIssue
from app.db.models.workflow_run import WorkflowRun
from app.services import audit_service
from app.services.jira_review_comment_parser import parse_qswarm_review_comment
from app.services.test_design_evolution_service import (
    apply_comment_driven_evolution,
    compute_new_test_design_json,
)
from app.services import test_design_version_service as tdv


_PROCESSED = "processed"
_FAILED = "failed"


def get_review_issue_for_run(db: Session, run_id: uuid.UUID) -> JiraTestDesignReviewIssue | None:
    return db.scalar(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == run_id)
    )


def list_comment_events_for_api(db: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(JiraReviewCommentEvent)
            .where(JiraReviewCommentEvent.workflow_run_id == run_id)
            .order_by(JiraReviewCommentEvent.created_at.asc())
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "review_issue_key": r.review_issue_key,
            "jira_comment_id": r.jira_comment_id,
            "author_account_id": r.author_account_id,
            "raw_comment_text": r.raw_comment_text[:2000],
            "parsed_action_type": r.parsed_action_type,
            "target_scope": r.target_scope,
            "reviewed_version_id": str(r.reviewed_version_id) if r.reviewed_version_id else None,
            "processed_status": r.processed_status,
            "response_comment_id": r.response_comment_id,
            "error_detail": r.error_detail,
            "created_feedback_id": str(r.created_feedback_id) if r.created_feedback_id else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def _processed_comment_ids(db: Session, run_id: uuid.UUID) -> set[str]:
    rows = db.scalars(
        select(JiraReviewCommentEvent.jira_comment_id).where(
            JiraReviewCommentEvent.workflow_run_id == run_id,
            JiraReviewCommentEvent.processed_status.in_((_PROCESSED, _FAILED)),
        )
    ).all()
    return {str(x) for x in rows if x}


def _unknown_clarification_lines() -> list[str]:
    return [
        "QSwarm could not map this comment to refine/regenerate.",
        "Try e.g. `@QSwarm add more negative scenarios` or `@QSwarm regenerate as minimal positive only`.",
    ]


def process_jira_review_comments(
    db: Session,
    jira: JiraClient,
    *,
    workflow_run_id: uuid.UUID,
) -> dict[str, Any]:
    run = db.get(WorkflowRun, workflow_run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != WorkflowRunStatus.AWAITING_APPROVAL.value:
        raise ValueError("invalid_run_state")
    appr = db.scalar(
        select(Approval).where(
            Approval.workflow_run_id == workflow_run_id,
            Approval.status == ApprovalStatus.PENDING.value,
        )
    )
    if appr is None:
        raise ValueError("no_pending_approval")

    rev = get_review_issue_for_run(db, workflow_run_id)
    if rev is None or rev.publish_status != "published" or not rev.review_jira_issue_key:
        raise ValueError("no_review_issue")

    review_key = rev.review_jira_issue_key.strip().upper()
    processed_ids = _processed_comment_ids(db, workflow_run_id)
    comments = jira.list_issue_comments(review_key)

    eligible: list[dict[str, Any]] = []
    for c in comments:
        cid = str(c.get("id") or "")
        body = str(c.get("body_text") or "")
        if not cid or cid in processed_ids:
            continue
        if "@qswarm" not in body.lower():
            continue
        eligible.append(c)

    eligible.sort(key=lambda x: str(x.get("created") or ""))

    processed_count = 0
    skipped_duplicates = 0
    errors: list[str] = []

    for c in eligible:
        cid = str(c.get("id") or "")
        body = str(c.get("body_text") or "")
        author = c.get("author_account_id")

        existing = db.scalar(
            select(JiraReviewCommentEvent).where(
                JiraReviewCommentEvent.workflow_run_id == workflow_run_id,
                JiraReviewCommentEvent.jira_comment_id == cid,
            )
        )
        if existing:
            skipped_duplicates += 1
            continue

        parsed = parse_qswarm_review_comment(body)
        if parsed is None:
            continue

        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_REVIEW_COMMENT_DETECTED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id="jira_review_comment_processor",
            workflow_run_id=workflow_run_id,
            step_name="jira_review_process_comments",
            entity_type="jira_comment",
            entity_id=cid,
            payload={"review_issue_key": review_key},
        )
        db.flush()

        current = tdv.get_current_version(db, workflow_run_id)
        reviewed_vid = current.id if current else None

        if parsed["parsed_action_type"] == "unknown":
            lines = _unknown_clarification_lines()
            try:
                rid = jira.add_comment(review_key, plain_lines_to_adf(lines))
            except JiraClientError as e:
                err = str(e)[:2000]
                row = JiraReviewCommentEvent(
                    workflow_run_id=workflow_run_id,
                    review_issue_key=review_key,
                    jira_comment_id=cid,
                    author_account_id=str(author)[:128] if author else None,
                    raw_comment_text=body[:8000],
                    parsed_action_type="unknown",
                    target_scope=parsed.get("target_scope"),
                    reviewed_version_id=reviewed_vid,
                    processed_status=_FAILED,
                    response_comment_id=None,
                    error_detail=err,
                    created_feedback_id=None,
                )
                db.add(row)
                db.flush()
                errors.append(f"{cid}:{err[:120]}")
                audit_service.write_audit(
                    db,
                    event_type=AuditEventType.JIRA_REVIEW_COMMENT_PROCESSING_FAILED.value,
                    actor_type=ActorType.SYSTEM.value,
                    actor_id="jira_review_comment_processor",
                    workflow_run_id=workflow_run_id,
                    step_name="jira_review_process_comments",
                    entity_type="jira_review_comment_event",
                    entity_id=str(row.id),
                    payload={"jira_comment_id": cid},
                )
                db.flush()
                continue

            row = JiraReviewCommentEvent(
                workflow_run_id=workflow_run_id,
                review_issue_key=review_key,
                jira_comment_id=cid,
                author_account_id=str(author)[:128] if author else None,
                raw_comment_text=body[:8000],
                parsed_action_type="unknown",
                target_scope=parsed.get("target_scope"),
                reviewed_version_id=reviewed_vid,
                processed_status=_PROCESSED,
                response_comment_id=str(rid) if rid else None,
                error_detail=None,
                created_feedback_id=None,
            )
            db.add(row)
            db.flush()
            processed_count += 1
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_REVIEW_COMMENT_PROCESSED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id="jira_review_comment_processor",
                workflow_run_id=workflow_run_id,
                step_name="jira_review_process_comments",
                entity_type="jira_review_comment_event",
                entity_id=str(row.id),
                payload={"jira_comment_id": cid, "action": "unknown"},
            )
            db.flush()
            continue

        action = parsed["parsed_action_type"]
        assert action in ("refine", "regenerate")
        instruction = parsed["instruction_text"]
        target_scope = parsed.get("target_scope")

        current_art = None
        if current:
            current_art = db.get(AgentArtifact, current.artifact_id)
        if current_art is None or not current_art.content_json:
            row = JiraReviewCommentEvent(
                workflow_run_id=workflow_run_id,
                review_issue_key=review_key,
                jira_comment_id=cid,
                author_account_id=str(author)[:128] if author else None,
                raw_comment_text=body[:8000],
                parsed_action_type=action,
                target_scope=target_scope,
                reviewed_version_id=reviewed_vid,
                processed_status=_FAILED,
                response_comment_id=None,
                error_detail="current_artifact_missing",
                created_feedback_id=None,
            )
            db.add(row)
            db.flush()
            errors.append(f"{cid}:current_artifact_missing")
            continue

        try:
            new_json = compute_new_test_design_json(
                db, run, current_art, action=action, feedback_text=instruction
            )
        except ValueError as e:
            row = JiraReviewCommentEvent(
                workflow_run_id=workflow_run_id,
                review_issue_key=review_key,
                jira_comment_id=cid,
                author_account_id=str(author)[:128] if author else None,
                raw_comment_text=body[:8000],
                parsed_action_type=action,
                target_scope=target_scope,
                reviewed_version_id=reviewed_vid,
                processed_status=_FAILED,
                response_comment_id=None,
                error_detail=str(e)[:2000],
                created_feedback_id=None,
            )
            db.add(row)
            db.flush()
            errors.append(f"{cid}:{e!s}")
            continue

        actor = f"jira:{author}" if author else "jira:unknown"
        try:
            fb_row, _ver, delta_cid = apply_comment_driven_evolution(
                db,
                jira,
                workflow_run_id=workflow_run_id,
                action=action,
                feedback_text=instruction,
                target_scope=target_scope,
                actor_id=actor[:256],
                new_json=new_json,
            )
        except (JiraClientError, ValueError) as e:
            err = str(e)[:2000]
            row = JiraReviewCommentEvent(
                workflow_run_id=workflow_run_id,
                review_issue_key=review_key,
                jira_comment_id=cid,
                author_account_id=str(author)[:128] if author else None,
                raw_comment_text=body[:8000],
                parsed_action_type=action,
                target_scope=target_scope,
                reviewed_version_id=reviewed_vid,
                processed_status=_FAILED,
                response_comment_id=None,
                error_detail=err,
                created_feedback_id=None,
            )
            db.add(row)
            db.flush()
            errors.append(f"{cid}:{err[:120]}")
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_REVIEW_COMMENT_PROCESSING_FAILED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id="jira_review_comment_processor",
                workflow_run_id=workflow_run_id,
                step_name="jira_review_process_comments",
                entity_type="jira_review_comment_event",
                entity_id=str(row.id),
                payload={"jira_comment_id": cid, "error": err[:300]},
            )
            db.flush()
            continue

        row = JiraReviewCommentEvent(
            workflow_run_id=workflow_run_id,
            review_issue_key=review_key,
            jira_comment_id=cid,
            author_account_id=str(author)[:128] if author else None,
            raw_comment_text=body[:8000],
            parsed_action_type=action,
            target_scope=target_scope,
            reviewed_version_id=reviewed_vid,
            processed_status=_PROCESSED,
            response_comment_id=str(delta_cid) if delta_cid else None,
            error_detail=None,
            created_feedback_id=fb_row.id,
        )
        db.add(row)
        db.flush()

        processed_count += 1
        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_REVIEW_COMMENT_PROCESSED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id="jira_review_comment_processor",
            workflow_run_id=workflow_run_id,
            step_name="jira_review_process_comments",
            entity_type="jira_review_comment_event",
            entity_id=str(row.id),
            payload={"jira_comment_id": cid, "action": action},
        )
        db.flush()

    return {
        "ok": True,
        "workflow_run_id": str(workflow_run_id),
        "processed_count": processed_count,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors,
    }
