"""Tests for session ↔ job approve state alignment."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.constants import AutomationJobStatus, AutomationSessionStatus
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_session import AutomationSession
from app.services.automation_session_review_state import (
    build_session_approve_state_error_message,
    reconcile_job_for_session_approve,
)
from test_automation_jobs import (
    _patch_playwright_run_for_job_and_review,
    _playwright_fixture_repo,
    _stub_execution_run_factory,
)


def test_reconcile_stuck_executing_with_successful_execution(db_session):
    job = AutomationJob(
        approved_case_id="RECON-1",
        requested_by="u",
        status=AutomationJobStatus.EXECUTING.value,
        repo_path="/tmp/x",
        framework_summary_json={"framework_type": "playwright"},
        change_plan_json={"target_test_file": "tests/x.spec.ts", "files": []},
        generated_patch_json={"target_test_file": "tests/x.spec.ts", "files": []},
        execution_result_json={"success": True, "framework_type": "playwright", "notes": []},
    )
    db_session.add(job)
    db_session.flush()

    outcome = reconcile_job_for_session_approve(db_session, job)
    assert outcome == "reconciled"
    assert job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value


def test_reconcile_already_approved_is_noop(db_session):
    job = AutomationJob(
        approved_case_id="RECON-2",
        requested_by="u",
        status=AutomationJobStatus.APPROVED_FOR_PR.value,
    )
    db_session.add(job)
    db_session.flush()
    assert reconcile_job_for_session_approve(db_session, job) == "already_approved"


def test_build_session_approve_state_error_includes_raw_statuses():
    job = AutomationJob(
        approved_case_id="ERR-1",
        requested_by="u",
        status=AutomationJobStatus.AWAITING_HUMAN_INPUT.value,
        blocked_reason="Need clarification",
    )
    summary = {
        "status": AutomationSessionStatus.AWAITING_REVIEW.value,
        "job_status": job.status,
        "ui_status": "awaiting_review",
    }
    msg = build_session_approve_state_error_message(summary=summary, job=job)
    assert "job_status='awaiting_human_input'" in msg
    assert "session_status='awaiting_review'" in msg
    assert "ui_status='awaiting_review'" in msg


def test_ui_v1_approve_review_ready_session(client, tmp_path: Path, monkeypatch, db_session):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "UI-APR-1",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    sid = r.json()["id"]
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    ap = client.post(f"/api/v1/sessions/{sid}/approve", json={"actorId": "qa.lead"})
    assert ap.status_code == 200, ap.text
    body = ap.json()
    assert body["workflowStatus"] == AutomationSessionStatus.APPROVED_FOR_PR.value
    assert body["status"] == "queued"
    job = db_session.get(AutomationJob, uuid.UUID(r.json()["automation_job_id"]))
    assert job.status == AutomationJobStatus.APPROVED_FOR_PR.value
    sess = db_session.get(AutomationSession, uuid.UUID(sid))
    assert sess.status == AutomationSessionStatus.APPROVED_FOR_PR.value

    get_after = client.get(f"/api/v1/sessions/{sid}")
    assert get_after.status_code == 200
    assert get_after.json()["workflowStatus"] == AutomationSessionStatus.APPROVED_FOR_PR.value
    assert get_after.json()["status"] == "queued"
    approve_reviews = [r for r in get_after.json()["reviews"] if r.get("actionType") == "approve"]
    assert approve_reviews
    assert approve_reviews[-1]["status"] == "addressed"


def test_ui_v1_approve_after_revision(client, tmp_path: Path, monkeypatch, db_session):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "UI-APR-REV",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    sid = r.json()["id"]
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    assert (
        client.post(
            f"/api/v1/sessions/{sid}/request-revision",
            json={"actorId": "rev", "instructionText": "add assertion"},
        ).status_code
        == 200
    )

    ap = client.post(f"/api/v1/sessions/{sid}/approve", json={"actorId": "qa.lead"})
    assert ap.status_code == 200, ap.text
    job = db_session.get(AutomationJob, uuid.UUID(r.json()["automation_job_id"]))
    assert job.status == AutomationJobStatus.APPROVED_FOR_PR.value
    assert ap.json()["workflowStatus"] == AutomationSessionStatus.APPROVED_FOR_PR.value


def test_approve_reconciles_stuck_executing_hosted_like_session(client, tmp_path: Path, monkeypatch, db_session):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "UI-APR-STUCK",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    sid = uuid.UUID(r.json()["id"])
    jid = uuid.UUID(r.json()["automation_job_id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    job = db_session.get(AutomationJob, jid)
    job.status = AutomationJobStatus.EXECUTING.value
    db_session.commit()

    ap = client.post(f"/api/v1/sessions/{sid}/approve", json={"actorId": "qa.lead"})
    assert ap.status_code == 200, ap.text
    db_session.refresh(job)
    assert job.status == AutomationJobStatus.APPROVED_FOR_PR.value


def test_approve_idempotent_when_already_approved(client, tmp_path: Path, monkeypatch, db_session):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "UI-APR-IDEM",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    sid = r.json()["id"]
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/approve", json={"actorId": "qa"}).status_code == 200

    ap2 = client.post(f"/api/v1/sessions/{sid}/approve", json={"actorId": "qa"})
    assert ap2.status_code == 200, ap2.text
    assert ap2.json()["workflowStatus"] == AutomationSessionStatus.APPROVED_FOR_PR.value
    assert ap2.json()["status"] == "queued"


def test_approve_rejects_pending_with_clear_error(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "UI-APR-BAD",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    sid = r.json()["id"]
    ap = client.post(f"/api/v1/sessions/{sid}/approve", json={"actorId": "qa"})
    assert ap.status_code == 409
    detail = ap.json()["detail"]
    assert detail["code"] == "invalid_state"
    assert "job_status=" in detail["message"]
    assert "pending" in detail["message"]
