"""Workflow API tests (stub Jira, in-memory DB)."""

import uuid

from sqlalchemy import select

from app.core.constants import WorkflowRunStatus
from app.db.models.approval import Approval
from app.db.models.workflow_run import WorkflowRun


def test_workflow_create_and_start_happy_path(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-101", "initiated_by": "tester"},
    )
    assert r.status_code == 201
    body = r.json()
    run_id = body["id"]
    assert body["status"] == WorkflowRunStatus.PENDING.value

    r2 = client.post(f"/workflow/runs/{run_id}/start")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == WorkflowRunStatus.AWAITING_APPROVAL.value

    run = db_session.get(WorkflowRun, uuid.UUID(run_id))
    assert run is not None
    assert run.status == WorkflowRunStatus.AWAITING_APPROVAL.value
    assert run.jira_story_id is not None

    appr = db_session.execute(select(Approval).where(Approval.workflow_run_id == run.id)).scalar_one()
    assert appr.status == "pending"
