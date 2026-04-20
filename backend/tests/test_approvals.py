"""Approval endpoint tests."""

import uuid

from sqlalchemy import select

from app.core.constants import ApprovalStatus, WorkflowRunStatus
from app.db.models.approval import Approval


def _create_run_at_approval(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-200", "initiated_by": "tester"},
    )
    run_id = r.json()["id"]
    client.post(f"/workflow/runs/{run_id}/start")
    appr = db_session.execute(
        select(Approval).where(Approval.workflow_run_id == uuid.UUID(run_id))
    ).scalar_one()
    return str(appr.id), run_id


def test_approve(client, db_session):
    aid, run_id = _create_run_at_approval(client, db_session)
    r = client.post(
        f"/approvals/{aid}/approve",
        json={"actor_id": "reviewer_a", "notes": "LGTM"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == ApprovalStatus.APPROVED.value

    run_r = client.get(f"/workflow/runs/{run_id}")
    assert run_r.json()["status"] == WorkflowRunStatus.COMPLETED.value


def test_reject(client, db_session):
    aid, run_id = _create_run_at_approval(client, db_session)
    r = client.post(
        f"/approvals/{aid}/reject",
        json={"actor_id": "reviewer_b", "notes": "Needs more scenarios"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == ApprovalStatus.REJECTED.value

    run_r = client.get(f"/workflow/runs/{run_id}")
    assert run_r.json()["status"] == WorkflowRunStatus.REJECTED.value
