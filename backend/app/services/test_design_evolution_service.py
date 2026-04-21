"""Refine / regenerate Sprint 1 test design while awaiting QSwarm approval."""

from __future__ import annotations

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
from app.services.jira_draft_test_case_sync_service import sync_package_to_jira_draft_rows
from app.services.test_design_deterministic_feedback import (
    refine_test_design_content,
    regenerate_test_design_content,
)
from app.services.test_design_publish_builder import build_publish_package
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

    if action == "refine":
        new_json = refine_test_design_content(
            current_art.content_json if isinstance(current_art.content_json, dict) else {},
            feedback_text,
        )
    else:
        iid = gs.get("intake_artifact_id")
        if not iid:
            raise ValueError("missing_intake_artifact")
        intake_row = db.get(AgentArtifact, uuid.UUID(str(iid)))
        if intake_row is None or not intake_row.content_json:
            raise ValueError("intake_artifact_missing")
        new_json = regenerate_test_design_content(
            intake_row.content_json if isinstance(intake_row.content_json, dict) else {},
            feedback_text,
        )

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

    package = build_publish_package(
        parent_issue_key=parent_key,
        workflow_run_id=run.id,
        source_artifact_id=new_art.id,
        test_design_content_json=new_json,
    )
    if not package.cases:
        db.delete(new_art)
        db.flush()
        fb_row.error_detail = "no_cases_after_evolution"
        audit_service.write_audit(
            db,
            event_type=AuditEventType.TEST_DESIGN_REPUBLISH_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id[:256],
            workflow_run_id=run.id,
            step_name=f"test_design_{action}",
            entity_type="test_design_feedback",
            entity_id=str(fb_row.id),
            payload={"reason": "no_cases_after_evolution"},
        )
        db.flush()
        raise ValueError("no_cases_after_evolution")

    next_ver_num = tdv.compute_next_version_number(db, run.id)
    try:
        sync_package_to_jira_draft_rows(
            db,
            jira,
            settings,
            workflow_run_id=run.id,
            package=package,
            internal_sync_version=next_ver_num,
        )
    except JiraClientError as e:
        err = str(e)[:2000]
        fb_row.error_detail = err
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
            payload={"error": err[:500]},
        )
        db.flush()
        raise

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
    gs_out = dict(gs)
    gs_out["test_design_artifact_id"] = str(new_art.id)
    run.graph_state_json = gs_out
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

    return {
        "ok": True,
        "workflow_run_id": str(run.id),
        "new_version_number": next_ver_num,
        "action": action,
        "message": "Test design updated and Jira draft test cases synced.",
    }
