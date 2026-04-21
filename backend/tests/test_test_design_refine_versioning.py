"""Sprint 1 test design versions, refine/regenerate, Jira draft sync (stub)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

import app.connectors.jira_client as jira_mod
from app.connectors.jira_client import JiraClient, JiraClientError
from app.core.constants import AuditEventType, WorkflowRunStatus
from app.db.models.agent_artifact import AgentArtifact
from app.db.models.approval import Approval
from app.db.models.audit_log import AuditLog
from app.db.models.test_design_feedback import TestDesignFeedback as TDFeedback
from app.db.models.test_design_version import TestDesignVersion as TDVersion
from app.db.models.workflow_run import WorkflowRun


@pytest.fixture(autouse=True)
def _reset_stub_jira_create_counter():
    jira_mod._STUB_CREATE_SEQ[0] = 0
    yield


def _run_to_approval(client):
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-REF-1", "initiated_by": "tester"},
    )
    run_id = r.json()["id"]
    client.post(f"/workflow/runs/{run_id}/start")
    return run_id


def test_initial_version_after_sprint1_start(client, db_session):
    run_id = uuid.UUID(_run_to_approval(client))
    versions = db_session.scalars(
        select(TDVersion).where(TDVersion.workflow_run_id == run_id)
    ).all()
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].version_action == "initial"
    assert versions[0].is_current is True


def test_refine_creates_new_artifact_version_and_feedback(client, db_session):
    run_id = _run_to_approval(client)
    appr = db_session.execute(
        select(Approval).where(Approval.workflow_run_id == uuid.UUID(run_id))
    ).scalar_one()
    art_before = appr.artifact_id

    r = client.post(
        f"/workflow/runs/{run_id}/test-design/refine",
        json={"actor_id": "qa.lead", "feedback_text": "Add more negative scenarios and make steps stepwise."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["new_version_number"] == 2
    assert body["action"] == "refine"

    db_session.expire_all()
    appr2 = db_session.get(Approval, appr.id)
    assert appr2.artifact_id != art_before

    versions = db_session.scalars(
        select(TDVersion).where(TDVersion.workflow_run_id == uuid.UUID(run_id))
    ).all()
    assert len(versions) == 2
    assert sum(1 for v in versions if v.is_current) == 1
    cur = next(v for v in versions if v.is_current)
    assert cur.version_number == 2
    assert cur.parent_version_id is not None

    fb = db_session.scalars(select(TDFeedback).where(TDFeedback.workflow_run_id == uuid.UUID(run_id))).all()
    assert len(fb) == 1
    assert fb[0].action_type == "refine"
    assert fb[0].error_detail is None

    run = db_session.get(WorkflowRun, uuid.UUID(run_id))
    assert run.status == WorkflowRunStatus.AWAITING_APPROVAL.value


def test_regenerate_creates_new_version(client, db_session):
    run_id = _run_to_approval(client)
    r = client.post(
        f"/workflow/runs/{run_id}/test-design/regenerate",
        json={"actor_id": "qa.lead", "feedback_text": "Regenerate as minimal positive only."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["new_version_number"] == 2
    versions = db_session.scalars(
        select(TDVersion).where(TDVersion.workflow_run_id == uuid.UUID(run_id))
    ).all()
    assert len(versions) == 2
    fb = db_session.scalars(select(TDFeedback).where(TDFeedback.workflow_run_id == uuid.UUID(run_id))).all()
    assert fb[0].action_type == "regenerate"


def test_refine_calls_jira_update_issue(client, db_session, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def capture(self, issue_key: str, **kwargs):
        calls.append((issue_key, kwargs))

    monkeypatch.setattr(JiraClient, "update_issue", capture)
    run_id = _run_to_approval(client)
    r = client.post(
        f"/workflow/runs/{run_id}/test-design/refine",
        json={"actor_id": "qa.lead", "feedback_text": "Make steps more detailed."},
    )
    assert r.status_code == 200, r.text
    assert len(calls) >= 1
    assert all(kw.get("summary") for _, kw in calls if kw)


def test_refine_blocked_after_completed(client, db_session):
    run_id = _run_to_approval(client)
    appr = db_session.execute(
        select(Approval).where(Approval.workflow_run_id == uuid.UUID(run_id))
    ).scalar_one()
    client.post(f"/approvals/{appr.id}/approve", json={"actor_id": "rev", "notes": "ok"})
    r = client.post(
        f"/workflow/runs/{run_id}/test-design/refine",
        json={"actor_id": "qa.lead", "feedback_text": "Too late"},
    )
    assert r.status_code == 409


def test_refine_blocked_after_reject(client, db_session):
    run_id = _run_to_approval(client)
    appr = db_session.execute(
        select(Approval).where(Approval.workflow_run_id == uuid.UUID(run_id))
    ).scalar_one()
    client.post(f"/approvals/{appr.id}/reject", json={"actor_id": "rev", "notes": "no"})
    r = client.post(
        f"/workflow/runs/{run_id}/test-design/refine",
        json={"actor_id": "qa.lead", "feedback_text": "Too late"},
    )
    assert r.status_code == 409


def test_read_version_and_feedback_endpoints(client, db_session):
    run_id = _run_to_approval(client)
    client.post(
        f"/workflow/runs/{run_id}/test-design/refine",
        json={"actor_id": "a", "feedback_text": "negative cases", "target_scope": "all"},
    )
    vr = client.get(f"/workflow/runs/{run_id}/test-design/versions")
    assert vr.status_code == 200
    assert len(vr.json()["items"]) == 2
    fr = client.get(f"/workflow/runs/{run_id}/test-design/feedback")
    assert fr.status_code == 200
    assert len(fr.json()["items"]) == 1
    assert fr.json()["items"][0]["target_scope"] == "all"


def test_jira_update_failure_does_not_advance_version(client, db_session, monkeypatch):
    run_id = _run_to_approval(client)

    def boom(self, issue_key: str, **kwargs):
        raise JiraClientError("update failed", status_code=400)

    monkeypatch.setattr(JiraClient, "update_issue", boom)
    r = client.post(
        f"/workflow/runs/{run_id}/test-design/refine",
        json={"actor_id": "qa.lead", "feedback_text": "negative"},
    )
    assert r.status_code == 502

    db_session.expire_all()
    versions = db_session.scalars(
        select(TDVersion).where(TDVersion.workflow_run_id == uuid.UUID(run_id))
    ).all()
    assert len(versions) == 1
    assert versions[0].version_number == 1

    fb = db_session.scalars(select(TDFeedback).where(TDFeedback.workflow_run_id == uuid.UUID(run_id))).all()
    assert len(fb) == 1
    assert fb[0].error_detail

    appr = db_session.execute(
        select(Approval).where(Approval.workflow_run_id == uuid.UUID(run_id))
    ).scalar_one()
    art = db_session.get(AgentArtifact, appr.artifact_id)
    assert art.agent_name == "test_design_agent"

    n_failed = db_session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.workflow_run_id == uuid.UUID(run_id),
            AuditLog.event_type == AuditEventType.TEST_DESIGN_REPUBLISH_FAILED.value,
        )
    )
    assert (n_failed or 0) >= 1
