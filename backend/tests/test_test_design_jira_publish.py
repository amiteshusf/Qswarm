"""Sprint 1 test design → single Jira draft review Task (stub Jira, in-memory DB)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

import app.connectors.jira_client as jira_mod
from app.connectors.jira_client import JiraClient, JiraClientError
from app.core.constants import WorkflowRunStatus
from app.db.models.approval import Approval
from app.db.models.jira_generated_test_case import JiraGeneratedTestCase
from app.db.models.jira_test_design_review_issue import JiraTestDesignReviewIssue
from app.db.models.workflow_run import WorkflowRun


@pytest.fixture(autouse=True)
def _reset_stub_jira():
    jira_mod._STUB_CREATE_SEQ[0] = 0
    jira_mod._STUB_COMMENT_SEQ[0] = 0
    jira_mod._STUB_COMMENTS_BY_ISSUE.clear()
    yield


def test_publish_creates_single_review_issue_and_reaches_approval(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-101", "initiated_by": "tester"},
    )
    run_id = uuid.UUID(r.json()["id"])
    r2 = client.post(f"/workflow/runs/{run_id}/start")
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == WorkflowRunStatus.AWAITING_APPROVAL.value

    legacy = db_session.scalars(
        select(JiraGeneratedTestCase).where(JiraGeneratedTestCase.workflow_run_id == run_id)
    ).all()
    assert len(legacy) == 0

    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == run_id)
    ).one()
    assert rev.publish_status == "published"
    assert rev.review_jira_issue_key
    assert rev.parent_jira_issue_key == "QSW-101"

    jr = client.get(f"/workflow/runs/{run_id}/jira-review")
    assert jr.status_code == 200
    assert jr.json()["review_jira_issue_key"] == rev.review_jira_issue_key

    appr = db_session.execute(select(Approval).where(Approval.workflow_run_id == run_id)).scalar_one()
    assert appr.status == "pending"


def test_list_generated_test_cases_endpoint_empty(client, db_session):
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
    assert data["items"] == []


def test_reviewer_unset_review_issue_still_published(client, db_session):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-303", "initiated_by": "tester"},
    )
    run_id = uuid.UUID(r.json()["id"])
    client.post(f"/workflow/runs/{run_id}/start")
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == run_id)
    ).one()
    assert rev.publish_status == "published"


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
        rev = db_session.scalars(
            select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == run_id)
        ).one()
        assert rev.publish_status == "published"
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
