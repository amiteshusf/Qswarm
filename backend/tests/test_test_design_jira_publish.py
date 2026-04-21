"""Sprint 1 test design → Jira draft Task publish (stub Jira, in-memory DB)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

import app.connectors.jira_client as jira_mod
from app.connectors.jira_client import JiraClient, JiraClientError
from app.core.constants import WorkflowRunStatus
from app.db.models.approval import Approval
from app.db.models.jira_generated_test_case import JiraGeneratedTestCase
from app.db.models.workflow_run import WorkflowRun


@pytest.fixture(autouse=True)
def _reset_stub_jira_create_counter():
    jira_mod._STUB_CREATE_SEQ[0] = 0
    yield


def test_publish_creates_jira_rows_and_reaches_approval(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-101", "initiated_by": "tester"},
    )
    run_id = uuid.UUID(r.json()["id"])
    r2 = client.post(f"/workflow/runs/{run_id}/start")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == WorkflowRunStatus.AWAITING_APPROVAL.value

    rows = db_session.scalars(
        select(JiraGeneratedTestCase).where(JiraGeneratedTestCase.workflow_run_id == run_id)
    ).all()
    published = [x for x in rows if x.publish_status == "published"]
    assert len(published) >= 1
    assert all(x.parent_jira_issue_key == "QSW-101" for x in published)
    assert all(x.generated_jira_issue_key for x in published)

    appr = db_session.execute(select(Approval).where(Approval.workflow_run_id == run_id)).scalar_one()
    assert appr.status == "pending"


def test_list_generated_test_cases_endpoint(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-202", "initiated_by": "tester"},
    )
    run_id = r.json()["id"]
    client.post(f"/workflow/runs/{run_id}/start")
    gr = client.get(f"/workflow/runs/{run_id}/generated-test-cases")
    assert gr.status_code == 200
    data = gr.json()
    assert data["workflow_run_id"] == run_id
    assert len(data["items"]) >= 1
    assert data["items"][0]["parent_jira_issue_key"] == "QSW-202"


def test_reviewer_unset_leaves_null_reviewer_account(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-303", "initiated_by": "tester"},
    )
    run_id = uuid.UUID(r.json()["id"])
    client.post(f"/workflow/runs/{run_id}/start")
    rows = db_session.scalars(
        select(JiraGeneratedTestCase).where(JiraGeneratedTestCase.workflow_run_id == run_id)
    ).all()
    pub = [x for x in rows if x.publish_status == "published"]
    assert pub
    assert all(x.reviewer_account_id is None for x in pub)
    assert all(x.assignment_status == "skipped" for x in pub)


def test_assign_failure_still_reaches_approval(client, db_session, monkeypatch):
    orig = JiraClient.assign_issue

    def boom(self, issue_key, account_id):
        raise JiraClientError("assign failed", status_code=400)

    monkeypatch.setattr(JiraClient, "assign_issue", boom)
    monkeypatch.setenv("JIRA_DEFAULT_TEST_REVIEWER_ACCOUNT_ID", "fake-account-id")
    from app.core.config import get_settings

    get_settings.cache_clear()

    try:
        r = client.post(
            "/workflow/runs",
            json={"jira_issue_key": "QSW-404", "initiated_by": "tester"},
        )
        run_id = uuid.UUID(r.json()["id"])
        r2 = client.post(f"/workflow/runs/{run_id}/start")
        assert r2.status_code == 200
        rows = db_session.scalars(
            select(JiraGeneratedTestCase).where(JiraGeneratedTestCase.workflow_run_id == run_id)
        ).all()
        pub = [x for x in rows if x.publish_status == "published"]
        assert pub
        assert all(x.assignment_status == "failed" for x in pub)
    finally:
        monkeypatch.delenv("JIRA_DEFAULT_TEST_REVIEWER_ACCOUNT_ID", raising=False)
        get_settings.cache_clear()
        monkeypatch.setattr(JiraClient, "assign_issue", orig)


def test_create_issue_failure_fails_workflow_no_approval(client, db_session, monkeypatch):
    def boom(self, **kwargs):
        raise JiraClientError("create failed", status_code=400)

    monkeypatch.setattr(JiraClient, "create_issue", boom)
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-505", "initiated_by": "tester"},
    )
    run_id = uuid.UUID(r.json()["id"])
    r2 = client.post(f"/workflow/runs/{run_id}/start")
    assert r2.status_code == 200
    assert r2.json()["status"] == WorkflowRunStatus.FAILED.value

    run = db_session.get(WorkflowRun, run_id)
    assert run.status == WorkflowRunStatus.FAILED.value

    appr_rows = db_session.scalars(
        select(Approval).where(Approval.workflow_run_id == run_id)
    ).all()
    assert len(appr_rows) == 0
