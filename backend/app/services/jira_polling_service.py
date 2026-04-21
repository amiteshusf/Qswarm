"""Manual Jira search polling to create and start Sprint 1 workflow runs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.connectors.jira_client import JiraClient, JiraClientError
from app.core.constants import ActorType, AuditEventType
from app.schemas.jira_pickup import (
    JiraPickupPollResponse,
    JiraPickupResultItem,
    PICKUP_LABEL_DEFAULT,
)
from app.schemas.workflow import WorkflowRunCreateRequest
from app.services import audit_service, workflow_service
from app.services.jira_pickup_service import evaluate_pickup_candidate, jira_pickup_jql

PICKUP_ACTOR_ID = "system:jira-pickup"


def run_pickup_poll(
    db: Session,
    jira: JiraClient,
    *,
    limit: int = 10,
    label: str | None = None,
) -> JiraPickupPollResponse:
    """
    Search Jira for labeled Story/Task issues, run preflight, create+start runs for eligible keys.

    Does not schedule itself; intended for ``POST /jira/pickup/poll`` only.
    """
    lab = (label or PICKUP_LABEL_DEFAULT).strip()
    limit = max(1, min(limit, 100))
    jql = jira_pickup_jql(lab)

    audit_service.write_audit(
        db,
        event_type=AuditEventType.JIRA_PICKUP_POLL_STARTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=PICKUP_ACTOR_ID,
        workflow_run_id=None,
        step_name="poll",
        entity_type="jira_pickup",
        entity_id=None,
        payload={"label": lab, "limit": limit, "jql": jql},
    )
    db.commit()

    results: list[JiraPickupResultItem] = []
    try:
        search = jira.search_issues(jql, max_results=limit)
    except JiraClientError as e:
        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_PICKUP_POLL_COMPLETED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=PICKUP_ACTOR_ID,
            workflow_run_id=None,
            step_name="poll",
            entity_type="jira_pickup",
            entity_id=None,
            payload={"label": lab, "error": "jira_search_failed", "message": str(e)[:500]},
        )
        db.commit()
        return JiraPickupPollResponse(
            ok=False,
            label=lab,
            checked=0,
            picked_up=0,
            skipped=0,
            results=[
                JiraPickupResultItem(
                    issue_key="_",
                    action="error",
                    reason="jira_fetch_error",
                )
            ],
        )

    raw_issues: list[dict[str, Any]] = list(search.get("issues") or [])
    seen: set[str] = set()
    ordered_keys: list[dict[str, Any]] = []
    for row in raw_issues:
        k = str(row.get("issue_key") or "").strip().upper()
        if not k or k in seen:
            continue
        seen.add(k)
        ordered_keys.append(row)

    picked = 0
    skipped = 0

    for row in ordered_keys:
        issue_key = str(row.get("issue_key") or "").strip().upper()
        labels = list(row.get("labels") or [])
        issue_type = row.get("issue_type")
        status_category_key = row.get("status_category_key")
        summary = row.get("summary")

        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_PICKUP_CANDIDATE_FOUND.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=PICKUP_ACTOR_ID,
            workflow_run_id=None,
            step_name="preflight",
            entity_type="jira_issue",
            entity_id=issue_key,
            payload={"summary": (summary or "")[:200]},
        )
        db.flush()

        eligible, skip_reason = evaluate_pickup_candidate(
            issue_key=issue_key,
            labels=labels,
            issue_type=issue_type,
            status_category_key=status_category_key,
            summary=summary,
            db=db,
        )

        if not eligible:
            skipped += 1
            reason = skip_reason or "missing_label"
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PICKUP_SKIPPED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=PICKUP_ACTOR_ID,
                workflow_run_id=None,
                step_name="preflight",
                entity_type="jira_issue",
                entity_id=issue_key,
                payload={"reason": reason},
            )
            db.commit()
            results.append(
                JiraPickupResultItem(issue_key=issue_key, action="skipped", reason=reason)
            )
            continue

        try:
            run = workflow_service.create_run(
                db,
                WorkflowRunCreateRequest(
                    jira_issue_key=issue_key,
                    initiated_by=PICKUP_ACTOR_ID,
                ),
            )
            db.flush()
        except Exception as e:
            db.rollback()
            skipped += 1
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PICKUP_SKIPPED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=PICKUP_ACTOR_ID,
                workflow_run_id=None,
                step_name="create_run",
                entity_type="jira_issue",
                entity_id=issue_key,
                payload={"reason": "workflow_create_failed", "error": str(e)[:500]},
            )
            db.commit()
            results.append(
                JiraPickupResultItem(
                    issue_key=issue_key,
                    action="skipped",
                    reason="workflow_create_failed",
                )
            )
            continue

        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_PICKUP_WORKFLOW_CREATED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=PICKUP_ACTOR_ID,
            workflow_run_id=run.id,
            step_name="create_run",
            entity_type="workflow_run",
            entity_id=str(run.id),
            payload={"jira_issue_key": issue_key},
        )
        db.commit()

        try:
            workflow_service.start_run(db, run.id, jira)
        except ValueError as e:
            msg = str(e)
            skipped += 1
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PICKUP_SKIPPED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=PICKUP_ACTOR_ID,
                workflow_run_id=run.id,
                step_name="start_run",
                entity_type="workflow_run",
                entity_id=str(run.id),
                payload={"reason": "workflow_start_failed", "error": msg[:500]},
            )
            db.commit()
            results.append(
                JiraPickupResultItem(
                    issue_key=issue_key,
                    action="skipped",
                    reason="workflow_start_failed",
                )
            )
            continue
        except JiraClientError as e:
            skipped += 1
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PICKUP_SKIPPED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=PICKUP_ACTOR_ID,
                workflow_run_id=run.id,
                step_name="start_run",
                entity_type="workflow_run",
                entity_id=str(run.id),
                payload={"reason": "workflow_start_failed", "error": str(e)[:500]},
            )
            db.commit()
            results.append(
                JiraPickupResultItem(
                    issue_key=issue_key,
                    action="skipped",
                    reason="workflow_start_failed",
                )
            )
            continue
        except Exception as e:
            skipped += 1
            audit_service.write_audit(
                db,
                event_type=AuditEventType.JIRA_PICKUP_SKIPPED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=PICKUP_ACTOR_ID,
                workflow_run_id=run.id,
                step_name="start_run",
                entity_type="workflow_run",
                entity_id=str(run.id),
                payload={"reason": "workflow_start_failed", "error": str(e)[:500]},
            )
            db.commit()
            results.append(
                JiraPickupResultItem(
                    issue_key=issue_key,
                    action="skipped",
                    reason="workflow_start_failed",
                )
            )
            continue

        picked += 1
        audit_service.write_audit(
            db,
            event_type=AuditEventType.JIRA_PICKUP_WORKFLOW_STARTED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=PICKUP_ACTOR_ID,
            workflow_run_id=run.id,
            step_name="start_run",
            entity_type="workflow_run",
            entity_id=str(run.id),
            payload={"jira_issue_key": issue_key},
        )
        db.commit()
        results.append(
            JiraPickupResultItem(
                issue_key=issue_key,
                action="picked_up",
                workflow_run_id=run.id,
            )
        )

    audit_service.write_audit(
        db,
        event_type=AuditEventType.JIRA_PICKUP_POLL_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=PICKUP_ACTOR_ID,
        workflow_run_id=None,
        step_name="poll",
        entity_type="jira_pickup",
        entity_id=None,
        payload={
            "label": lab,
            "checked": len(ordered_keys),
            "picked_up": picked,
            "skipped": skipped,
        },
    )
    db.commit()

    return JiraPickupPollResponse(
        ok=True,
        label=lab,
        checked=len(ordered_keys),
        picked_up=picked,
        skipped=skipped,
        results=results,
    )
