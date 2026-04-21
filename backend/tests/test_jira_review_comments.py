"""Jira @QSwarm comment processing on the single draft review issue (stub)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

import app.connectors.jira_client as jira_mod
from app.connectors.jira_client import JiraClient
from app.core.constants import WorkflowRunStatus
from app.db.models.approval import Approval
from app.db.models.jira_review_comment_event import JiraReviewCommentEvent as CommentEvt
from app.db.models.jira_test_design_review_issue import JiraTestDesignReviewIssue
from app.db.models.test_design_feedback import TestDesignFeedback as TDFeedback
from app.db.models.test_design_version import TestDesignVersion as TDVersion
from app.db.models.workflow_run import WorkflowRun


@pytest.fixture(autouse=True)
def _reset_stub():
    jira_mod._STUB_CREATE_SEQ[0] = 0
    jira_mod._STUB_COMMENT_SEQ[0] = 0
    jira_mod._STUB_COMMENTS_BY_ISSUE.clear()
    yield


def _run_at_approval(client):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-CM-1", "initiated_by": "tester"},
    )
    run_id = r.json()["id"]
    client.post(f"/workflow/runs/{run_id}/start")
    return run_id


def test_process_comments_refine_creates_version_and_event(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    assert key

    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-1",
            "body_text": "@QSwarm add more negative scenarios",
            "author_account_id": "acc-1",
            "created": "2026-01-01T10:00:00.000+0000",
        }
    )

    pr = client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    assert pr.status_code == 200, pr.text
    assert pr.json()["processed_count"] == 1

    db_session.expire_all()
    versions = db_session.scalars(select(TDVersion).where(TDVersion.workflow_run_id == uuid.UUID(run_id))).all()
    assert len(versions) == 2
    evs = db_session.scalars(select(CommentEvt).where(CommentEvt.workflow_run_id == uuid.UUID(run_id))).all()
    assert len(evs) == 1
    assert evs[0].processed_status == "processed"
    assert evs[0].parsed_action_type == "refine"
    assert evs[0].created_feedback_id


def test_process_comments_regenerate(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-r1",
            "body_text": "@QSwarm regenerate as minimal positive only",
            "author_account_id": "acc-2",
            "created": "2026-01-02T10:00:00.000+0000",
        }
    )
    pr = client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    assert pr.status_code == 200
    fb = db_session.scalars(select(TDFeedback).where(TDFeedback.workflow_run_id == uuid.UUID(run_id))).all()
    assert any(f.action_type == "regenerate" for f in fb)


def test_duplicate_comment_not_processed_twice(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-dup",
            "body_text": "@QSwarm make steps more detailed",
            "author_account_id": "acc-3",
            "created": "2026-01-03T10:00:00.000+0000",
        }
    )
    assert client.post(f"/workflow/runs/{run_id}/jira-review/process-comments").json()["processed_count"] == 1
    assert client.post(f"/workflow/runs/{run_id}/jira-review/process-comments").json()["processed_count"] == 0

    evs = db_session.scalars(select(CommentEvt).where(CommentEvt.workflow_run_id == uuid.UUID(run_id))).all()
    assert len(evs) == 1


def test_non_tagged_comment_ignored(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-plain",
            "body_text": "LGTM from me",
            "author_account_id": "acc-4",
            "created": "2026-01-04T10:00:00.000+0000",
        }
    )
    pr = client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    assert pr.json()["processed_count"] == 0
    evs = db_session.scalars(select(CommentEvt).where(CommentEvt.workflow_run_id == uuid.UUID(run_id))).all()
    assert len(evs) == 0


def test_process_records_delta_comment_id(client, db_session, monkeypatch):
    posted: list[str] = []
    orig = JiraClient.add_comment

    def track(self, issue_key: str, body_adf):
        cid = orig(self, issue_key, body_adf)
        posted.append(str(cid))
        return cid

    monkeypatch.setattr(JiraClient, "add_comment", track)
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-delta",
            "body_text": "@QSwarm add more negative scenarios",
            "author_account_id": "acc-5",
            "created": "2026-01-05T10:00:00.000+0000",
        }
    )
    client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    assert posted
    ev = db_session.scalars(select(CommentEvt).where(CommentEvt.jira_comment_id == "ext-delta")).one()
    assert ev.response_comment_id


def test_workflow_stays_awaiting_after_process(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-stay",
            "body_text": "@QSwarm negative cases",
            "author_account_id": "acc-6",
            "created": "2026-01-06T10:00:00.000+0000",
        }
    )
    client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    run = db_session.get(WorkflowRun, uuid.UUID(run_id))
    assert run.status == WorkflowRunStatus.AWAITING_APPROVAL.value


def test_approval_after_comment_process(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-ap",
            "body_text": "@QSwarm add more negative scenarios",
            "author_account_id": "acc-7",
            "created": "2026-01-07T10:00:00.000+0000",
        }
    )
    client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    appr = db_session.execute(select(Approval).where(Approval.workflow_run_id == uuid.UUID(run_id))).scalar_one()
    r = client.post(f"/approvals/{appr.id}/approve", json={"actor_id": "rev", "notes": "ok"})
    assert r.status_code == 200


def test_list_jira_review_comments_endpoint(client, db_session):
    run_id = _run_at_approval(client)
    rev = db_session.scalars(
        select(JiraTestDesignReviewIssue).where(JiraTestDesignReviewIssue.workflow_run_id == uuid.UUID(run_id))
    ).one()
    key = rev.review_jira_issue_key
    jira_mod._STUB_COMMENTS_BY_ISSUE.setdefault(key.upper(), []).append(
        {
            "id": "ext-list",
            "body_text": "@QSwarm edge cases",
            "author_account_id": "acc-8",
            "created": "2026-01-08T10:00:00.000+0000",
        }
    )
    client.post(f"/workflow/runs/{run_id}/jira-review/process-comments")
    lr = client.get(f"/workflow/runs/{run_id}/jira-review/comments")
    assert lr.status_code == 200
    assert len(lr.json()["items"]) == 1
