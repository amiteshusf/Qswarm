"""Refine / regenerate Sprint 1 test design while awaiting QSwarm approval."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError
from app.core.config import Settings
from app.core.constants import (
    ActorType,
    ApprovalStatus,
    ArtifactType,
    AuditEventType,
    WorkflowRunStatus,
)
from app.db.models.agent_artifact import AgentArtifact
from app.db.models.approval import Approval
from app.db.models.test_design_feedback import TestDesignFeedback
from app.db.models.workflow_run import WorkflowRun
from app.services import audit_service
from app.services.jira_delta_comment_builder import build_delta_comment_lines
from app.services.jira_review_followup_service import post_delta_lines_on_review_issue
from app.services.test_design_deterministic_feedback import (
    refine_test_design_content,
    regenerate_test_design_content,
)
from app.services.test_design_publish_builder import draft_cases_from_test_design_json
from app.services import test_design_version_service as tdv


def _graph_state(run: WorkflowRun) -> dict[str, Any]:
    blob = run.graph_state_json or {}
    return blob if isinstance(blob, dict) else {}


def _get_pending_approval(db: Session, workflow_run_id: uuid.UUID) -> Approval | None:
    return db.scalar(
        select(Approval).where(
            Approval.workflow_run_id == workflow_run_id,
            Approval.status == ApprovalStatus.PENDING.value,
        )
    )


def _bootstrap_current_version_if_needed(db: Session, run: WorkflowRun):
    cur = tdv.get_current_version(db, run.id)
    if cur:
        return cur
    gs = _graph_state(run)
    tid = gs.get("test_design_artifact_id")
    if not tid:
        raise ValueError("no_test_design_version")
    return tdv.record_initial_version(
        db,
        workflow_run_id=run.id,
        artifact_id=uuid.UUID(str(tid)),
        created_by=run.initiated_by,
    )


def list_versions_for_api(db: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = tdv.list_versions(db, run_id)
    return [
        {
            "id": str(v.id),
            "artifact_id": str(v.artifact_id),
            "version_number": v.version_number,
            "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
            "version_action": v.version_action,
            "source_feedback_id": str(v.source_feedback_id) if v.source_feedback_id else None,
            "is_current": v.is_current,
            "created_by": v.created_by,
            "created_at": v.created_at.isoformat(),
            "notes": v.notes,
        }
        for v in rows
    ]


def list_feedback_for_api(db: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(TestDesignFeedback)
            .where(TestDesignFeedback.workflow_run_id == run_id)
            .order_by(TestDesignFeedback.created_at.asc())
        ).all()
    )
    return [
        {
            "id": str(f.id),
            "reviewed_version_id": str(f.reviewed_version_id) if f.reviewed_version_id else None,
            "action_type": f.action_type,
            "feedback_text": f.feedback_text,
            "actor_id": f.actor_id,
            "target_scope": f.target_scope,
            "error_detail": f.error_detail,
            "created_at": f.created_at.isoformat(),
        }
        for f in rows
    ]


def compute_new_test_design_json(
    db: Session,
    run: WorkflowRun,
    current_art: AgentArtifact,
    *,
    action: Literal["refine", "regenerate"],
    feedback_text: str,
) -> dict[str, Any]:
    gs = _graph_state(run)
    base = current_art.content_json if isinstance(current_art.content_json, dict) else {}
    if action == "refine":
        return refine_test_design_content(copy.deepcopy(base), feedback_text)
    iid = gs.get("intake_artifact_id")
    if not iid:
        raise ValueError("missing_intake_artifact")
    intake_row = db.get(AgentArtifact, uuid.UUID(str(iid)))
    if intake_row is None or not intake_row.content_json:
        raise ValueError("intake_artifact_missing")
    return regenerate_test_design_content(
        intake_row.content_json if isinstance(intake_row.content_json, dict) else {},
        feedback_text,
    )


def start_evolution_artifacts(
    db: Session,
    run: WorkflowRun,
    *,
    action: Literal["refine", "regenerate"],
    feedback_text: str,
    target_scope: str | None,
    actor_id: str,
    new_json: dict[str, Any],
) -> tuple[TestDesignFeedback, AgentArtifact, Any, AgentArtifact, dict[str, Any], int]:
    """
    Create feedback + new artifact; does **not** create a version row yet.

    Returns ``(fb_row, new_art, current_version_row, current_art, old_content, next_version_number)``.
    """
    current = _bootstrap_current_version_if_needed(db, run)
    current_art = db.get(AgentArtifact, current.artifact_id)
    if current_art is None or not current_art.content_json:
        raise ValueError("current_artifact_missing")
    old_content = copy.deepcopy(current_art.content_json) if isinstance(current_art.content_json, dict) else {}

    if not draft_cases_from_test_design_json(new_json):
        raise ValueError("no_cases_after_evolution")

    fb_row = TestDesignFeedback(
        workflow_run_id=run.id,
        reviewed_version_id=current.id,
        action_type=action,
        feedback_text=(feedback_text or "").strip() or "(empty)",
        actor_id=actor_id[:256],
        target_scope=(target_scope or None) if target_scope else None,
        error_detail=None,
    )
    db.add(fb_row)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.TEST_DESIGN_FEEDBACK_RECORDED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id[:256],
        workflow_run_id=run.id,
        step_name=f"test_design_{action}",
        entity_type="test_design_feedback",
        entity_id=str(fb_row.id),
        payload={"action": action, "target_scope": target_scope},
    )
    db.flush()

    new_art = AgentArtifact(
        workflow_run_id=run.id,
        agent_name="test_design_evolution",
        artifact_type=ArtifactType.TEST_DESIGN.value,
        version=(current_art.version or 1) + 1,
        content_json=new_json,
        content_text=None,
    )
    db.add(new_art)
    db.flush()

    next_ver_num = tdv.compute_next_version_number(db, run.id)
    return fb_row, new_art, current, current_art, old_content, next_ver_num


def finalize_evolution_version(
    db: Session,
    run: WorkflowRun,
    appr: Approval,
    *,
    current: Any,
    fb_row: TestDesignFeedback,
    new_art: AgentArtifact,
    action: Literal["refine", "regenerate"],
    actor_id: str,
    next_ver_num: int,
) -> Any:
    new_ver = tdv.create_new_version(
        db,
        workflow_run_id=run.id,
        artifact_id=new_art.id,
        version_number=next_ver_num,
        parent_version_id=current.id,
        version_action=action,
        source_feedback_id=fb_row.id,
        created_by=actor_id[:256],
        notes=None,
    )

    appr.artifact_id = new_art.id
    gs = dict(_graph_state(run))
    gs["test_design_artifact_id"] = str(new_art.id)
    run.graph_state_json = gs
    run.status = WorkflowRunStatus.AWAITING_APPROVAL.value
    run.current_step = "awaiting_approval"
    db.flush()

    ev = (
        AuditEventType.TEST_DESIGN_REFINED.value
        if action == "refine"
        else AuditEventType.TEST_DESIGN_REGENERATED.value
    )
    audit_service.write_audit(
        db,
        event_type=ev,
        actor_type=ActorType.USER.value,
        actor_id=actor_id[:256],
        workflow_run_id=run.id,
        step_name=f"test_design_{action}",
        entity_type="test_design_version",
        entity_id=str(new_ver.id),
        payload={"version_number": next_ver_num, "artifact_id": str(new_art.id)},
    )
    db.flush()
    return new_ver


def apply_comment_driven_evolution(
    db: Session,
    jira: JiraClient,
    *,
    workflow_run_id: uuid.UUID,
    action: Literal["refine", "regenerate"],
    feedback_text: str,
    target_scope: str | None,
    actor_id: str,
    new_json: dict[str, Any],
) -> tuple[TestDesignFeedback, int, str | None]:
    """Used by Jira comment processor after computing ``new_json``. Returns delta Jira comment id if posted."""
    run = db.get(WorkflowRun, workflow_run_id)
    if run is None:
        raise ValueError("run_not_found")
    appr = _get_pending_approval(db, run.id)
    if appr is None:
        raise ValueError("no_pending_approval")

    fb_row, new_art, current, _current_art, old_content, next_ver_num = start_evolution_artifacts(
        db,
        run,
        action=action,
        feedback_text=feedback_text,
        target_scope=target_scope,
        actor_id=actor_id,
        new_json=new_json,
    )

    delta_lines = build_delta_comment_lines(
        before=old_content,
        after=new_json,
        action=action,
        feedback_text=feedback_text,
        new_version_number=next_ver_num,
    )
    rid: str | None = None
    try:
        rid = post_delta_lines_on_review_issue(db, jira, workflow_run_id=run.id, lines=delta_lines)
        if rid:
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_REVIEW_DELTA_COMMENT_POSTED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id="jira_review_comment_processor",
                workflow_run_id=run.id,
                step_name="jira_review_process_comments",
                entity_type="jira_comment",
                entity_id=str(rid),
                payload={"review": "delta_reply"},
            )
            db.flush()
    except JiraClientError as e:
        err = str(e)[:2000]
        fb_row.error_detail = err
        # Roll back tentative artifact only; keep feedback row for audit / GET feedback API.
        db.delete(new_art)
        db.flush()
        audit_service.write_audit(
            db,
            event_type=AuditEventType.TEST_DESIGN_REPUBLISH_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id[:256],
            workflow_run_id=run.id,
            step_name=f"test_design_{action}",
            entity_type="test_design_feedback",
            entity_id=str(fb_row.id),
            payload={"error": err[:500], "phase": "jira_delta_comment"},
        )
        db.flush()
        raise

    finalize_evolution_version(
        db,
        run,
        appr,
        current=current,
        fb_row=fb_row,
        new_art=new_art,
        action=action,
        actor_id=actor_id,
        next_ver_num=next_ver_num,
    )
    return fb_row, next_ver_num, rid


def apply_workspace_evolution(
    db: Session,
    run: WorkflowRun,
    *,
    appr: Approval,
    action: Literal["refine", "regenerate"],
    feedback_text: str,
    target_scope: str | None,
    actor_id: str,
    new_json: dict[str, Any],
) -> tuple[TestDesignFeedback, int]:
    """QSwarm UI revision path — same versioning as Jira, without mandatory Jira delta post."""
    fb_row, new_art, current, _current_art, _old_content, next_ver_num = start_evolution_artifacts(
        db,
        run,
        action=action,
        feedback_text=feedback_text,
        target_scope=target_scope,
        actor_id=actor_id,
        new_json=new_json,
    )
    finalize_evolution_version(
        db,
        run,
        appr,
        current=current,
        fb_row=fb_row,
        new_art=new_art,
        action=action,
        actor_id=actor_id,
        next_ver_num=next_ver_num,
    )
    return fb_row, next_ver_num


def evolve_test_design(
    db: Session,
    jira: JiraClient,
    settings: Settings,
    *,
    workflow_run_id: uuid.UUID,
    action: Literal["refine", "regenerate"],
    actor_id: str,
    feedback_text: str,
    target_scope: str | None,
) -> dict[str, Any]:
    del settings  # reserved for symmetry with callers using SettingsDep
    run = db.get(WorkflowRun, workflow_run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != WorkflowRunStatus.AWAITING_APPROVAL.value:
        raise ValueError("invalid_run_state")
    appr = _get_pending_approval(db, run.id)
    if appr is None:
        raise ValueError("no_pending_approval")

    current = _bootstrap_current_version_if_needed(db, run)
    current_art = db.get(AgentArtifact, current.artifact_id)
    if current_art is None or not current_art.content_json:
        raise ValueError("current_artifact_missing")

    gs = _graph_state(run)
    parent_key = (gs.get("jira_issue_key") or "").strip().upper()
    if not parent_key:
        raise ValueError("missing_jira_issue_key")

    new_json = compute_new_test_design_json(db, run, current_art, action=action, feedback_text=feedback_text)

    fb_row, next_ver_num, _delta_id = apply_comment_driven_evolution(
        db,
        jira,
        workflow_run_id=workflow_run_id,
        action=action,
        feedback_text=feedback_text,
        target_scope=target_scope,
        actor_id=actor_id,
        new_json=new_json,
    )

    return {
        "ok": True,
        "workflow_run_id": str(run.id),
        "new_version_number": next_ver_num,
        "action": action,
        "message": "Test design updated; delta posted on Jira review issue.",
    }
