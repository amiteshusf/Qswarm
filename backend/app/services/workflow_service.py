"""Workflow run lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient
from app.core.constants import ActorType, AuditEventType, WorkflowRunStatus
from app.db.models.workflow_run import WorkflowRun
from app.schemas.workflow import WorkflowRunCreateRequest
from app.services import audit_service
from app.workflows.sprint1_graph import Sprint1Runner, build_sprint1_graph


def _graph_state_from_run(run: WorkflowRun) -> dict[str, Any]:
    blob = run.graph_state_json or {}
    return blob if isinstance(blob, dict) else {}


def run_to_response(run: WorkflowRun) -> dict[str, Any]:
    gs = _graph_state_from_run(run)
    issue_key = gs.get("jira_issue_key")
    return {
        "id": run.id,
        "jira_story_id": run.jira_story_id,
        "jira_issue_key": issue_key,
        "workflow_name": run.workflow_name,
        "status": run.status,
        "current_step": run.current_step,
        "initiated_by": run.initiated_by,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def create_run(db: Session, body: WorkflowRunCreateRequest) -> WorkflowRun:
    key = body.jira_issue_key.strip().upper()
    run = WorkflowRun(
        workflow_name="sprint1",
        status=WorkflowRunStatus.PENDING.value,
        current_step=None,
        graph_state_json={
            "jira_issue_key": key,
            "initiated_by": body.initiated_by,
        },
        initiated_by=body.initiated_by,
    )
    db.add(run)
    db.flush()
    return run


def get_run(db: Session, run_id: uuid.UUID) -> WorkflowRun | None:
    return db.get(WorkflowRun, run_id)


def _final_state_to_graph_json(final: dict[str, Any]) -> dict[str, Any]:
    """Persist a JSON-serializable subset of LangGraph output into `graph_state_json`."""
    out: dict[str, Any] = {}
    for k in (
        "jira_issue_key",
        "initiated_by",
        "intake_artifact_id",
        "test_design_artifact_id",
        "approval_id",
        "approval_status",
    ):
        if k in final and final[k] is not None:
            out[k] = final[k]
    if final.get("errors"):
        out["errors"] = list(final["errors"])
    return out


def start_run(db: Session, run_id: uuid.UUID, jira_client: JiraClient) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError("run_not_found")
    if run.status != WorkflowRunStatus.PENDING.value:
        raise ValueError("run_not_pending")

    gs = _graph_state_from_run(run)
    issue_key = gs.get("jira_issue_key")
    if not issue_key:
        raise ValueError("missing_jira_issue_key")
    initiated_by = gs.get("initiated_by") or run.initiated_by

    now = datetime.now(timezone.utc)
    run.status = WorkflowRunStatus.RUNNING.value
    run.started_at = now
    run.current_step = "fetch_story"
    run.error_message = None
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.WORKFLOW_STARTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=initiated_by,
        workflow_run_id=run.id,
        step_name="start",
        entity_type="workflow_run",
        entity_id=str(run.id),
        payload={"jira_issue_key": issue_key},
    )
    db.commit()

    runner = Sprint1Runner(db, jira_client)
    graph = build_sprint1_graph(runner)
    initial: dict[str, Any] = {
        "run_id": str(run.id),
        "jira_issue_key": issue_key,
        "initiated_by": initiated_by,
        "errors": [],
    }

    try:
        final = graph.invoke(initial)
    except Exception as e:
        db.rollback()
        run = db.get(WorkflowRun, run_id)
        if run:
            run.status = WorkflowRunStatus.FAILED.value
            run.error_message = str(e)[:2000]
            run.current_step = "failed"
            audit_service.write_audit(
                db,
                event_type=AuditEventType.WORKFLOW_FAILED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=initiated_by,
                workflow_run_id=run.id,
                step_name="failed",
                entity_type="workflow_run",
                entity_id=str(run.id),
                payload={"error": str(e)[:500]},
            )
            db.commit()
        raise

    merged_state = {**gs, **_final_state_to_graph_json(final)}
    run.graph_state_json = merged_state

    errs = final.get("errors") or []
    if errs:
        run.status = WorkflowRunStatus.FAILED.value
        run.error_message = "; ".join(errs)[:2000]
        run.current_step = "failed"
        audit_service.write_audit(
            db,
            event_type=AuditEventType.WORKFLOW_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=initiated_by,
            workflow_run_id=run.id,
            step_name="failed",
            entity_type="workflow_run",
            entity_id=str(run.id),
            payload={"errors": errs},
        )

    db.flush()
    return run
