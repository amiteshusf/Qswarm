"""Tests for plan-first automation flow (prepare-plan, approve-plan, start execution)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.services.ui_v1_session_brief_service import build_session_brief_for_ui
from app.services.ui_v1_session_review_service import _derive_next_actions
from test_automation_jobs import _ensure_git_repo_for_session_pr, _patch_playwright_run_for_job_and_review, _playwright_fixture_repo, _stub_execution_run_factory


@pytest.fixture
def ui_client(db_session):
    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _create_session(ui_client, tmp_path: Path) -> str:
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "PLAN-1",
            "engine": "stub",
            "createdBy": "qa",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["Login"],
        },
    )
    assert c.status_code == 201, c.text
    return c.json()["id"]


def test_plan_first_flow_prepare_approve_run(ui_client, tmp_path, monkeypatch, db_session):
    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())

    sid = _create_session(ui_client, tmp_path)

    prep = ui_client.post(f"/api/v1/sessions/{sid}/prepare-plan", json={"actorId": "qa"})
    assert prep.status_code == 200, prep.text
    prep_body = prep.json()
    assert prep_body["workflowStatus"] == "plan_ready"

    brief = build_session_brief_for_ui(db_session, uuid.UUID(sid))
    assert brief["automationBrief"]["available"] is True
    assert brief["sessionState"]["planApproved"] is False
    assert "approve_plan" in brief["sessionState"]["nextActions"]

    approve = ui_client.post(f"/api/v1/sessions/{sid}/approve-plan", json={"actorId": "qa"})
    assert approve.status_code == 200, approve.text
    assert approve.json()["workflowStatus"] == "plan_ready"

    brief2 = build_session_brief_for_ui(db_session, uuid.UUID(sid))
    assert brief2["sessionState"]["planApproved"] is True
    assert "start_automation" in brief2["sessionState"]["nextActions"]

    run = ui_client.post(f"/api/v1/sessions/{sid}/start", json={"actorId": "qa"})
    assert run.status_code == 200, run.text
    assert run.json()["status"] in ("awaiting_review", "running", "succeeded")


def test_start_without_plan_approval_blocked_on_plan_ready(ui_client, tmp_path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    sid = _create_session(ui_client, tmp_path)

    prep = ui_client.post(f"/api/v1/sessions/{sid}/prepare-plan", json={"actorId": "qa"})
    assert prep.status_code == 200, prep.text

    run = ui_client.post(f"/api/v1/sessions/{sid}/start", json={"actorId": "qa"})
    assert run.status_code == 409
    assert run.json()["detail"]["code"] == "plan_not_approved"


def test_legacy_start_still_runs_full_pipeline(ui_client, tmp_path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    sid = _create_session(ui_client, tmp_path)

    run = ui_client.post(f"/api/v1/sessions/{sid}/start", json={"actorId": "qa"})
    assert run.status_code == 200, run.text
    assert run.json()["status"] in ("awaiting_review", "running", "succeeded")


def test_derive_next_actions_plan_ready_states(db_session, tmp_path):
    from app.db.models.automation_job import AutomationJob
    from app.db.models.automation_session import AutomationSession
    from app.services.automation_session_service import session_to_summary

    job = AutomationJob(
        approved_case_id="X",
        requested_by="qa",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="awaiting_plan_approval",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        coding_engine="stub",
        status="plan_ready",
        created_by="qa",
    )
    db_session.add(sess)
    db_session.commit()

    summary = session_to_summary(db_session, sess)
    assert _derive_next_actions(summary) == ["approve_plan", "request_plan_revision"]

    sess.plan_approved_at = sess.created_at
    db_session.commit()
    summary2 = session_to_summary(db_session, sess)
    assert _derive_next_actions(summary2) == ["start_automation"]
