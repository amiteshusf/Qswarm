"""Tests for GET /api/v1/sessions/{id}/review-data (review cockpit payload)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models.automation_execution_attempt import AutomationExecutionAttempt
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_patch_version import AutomationPatchVersion
from app.db.models.automation_review_request import AutomationReviewRequest
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.db.models.code_review_request import CodeReviewRequest
from app.db.models.repository_connection import RepositoryConnection
from app.db.session import get_db
from app.main import app
from app.services.ui_v1_session_review_service import build_session_review_data_for_ui
from test_automation_jobs import _ensure_git_repo_for_session_pr, _playwright_fixture_repo


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


def _patch_json(files: list[dict]) -> dict:
    return {
        "framework_type": "playwright",
        "target_test_file": "tests/smoke.spec.ts",
        "generated_files": files,
    }


def _session_with_rounds(db_session, tmp_path: Path) -> tuple[uuid.UUID, AutomationRevisionRound, AutomationRevisionRound]:
    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    from app.db.models.automation_job import AutomationJob

    job = AutomationJob(
        approved_case_id="review-case",
        requested_by="qa",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="awaiting_automation_review",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        coding_engine="stub",
        status="awaiting_review",
        current_round_number=2,
        created_by="qa",
        source_reference="REV-1",
    )
    db_session.add(sess)
    db_session.flush()
    rnd1 = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="qa",
        trigger_type="initial",
        status="completed",
    )
    rnd2 = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=2,
        started_by="qa",
        trigger_type="review_revision",
        instruction_text="Fix login test",
        status="completed",
    )
    db_session.add_all([rnd1, rnd2])
    db_session.flush()
    return sess.id, rnd1, rnd2


def test_review_data_changed_files_with_previous_patch_version(db_session, tmp_path: Path):
    sid, rnd1, rnd2 = _session_with_rounds(db_session, tmp_path)
    pv1 = AutomationPatchVersion(
        automation_session_id=sid,
        revision_round_id=rnd1.id,
        version_number=1,
        patch_json=_patch_json(
            [{"path": "tests/login.spec.ts", "action": "modify", "content": "export const v1 = 1;\n"}]
        ),
        is_current=False,
        created_by="qa",
    )
    pv2 = AutomationPatchVersion(
        automation_session_id=sid,
        revision_round_id=rnd2.id,
        version_number=2,
        patch_json=_patch_json(
            [{"path": "tests/login.spec.ts", "action": "modify", "content": "export const v2 = 2;\n"}]
        ),
        is_current=True,
        created_by="qa",
    )
    db_session.add_all([pv1, pv2])
    db_session.commit()

    data = build_session_review_data_for_ui(db_session, sid)
    assert data["reviewSummary"]["currentPatchVersion"] == 2
    assert data["reviewSummary"]["changedFilesCount"] == 1
    assert data["reviewSummary"]["nextActions"] == ["request_revision", "approve"]

    files = data["changedFiles"]
    assert len(files) == 1
    f = files[0]
    assert f["path"] == "tests/login.spec.ts"
    assert f["currentContent"] == "export const v2 = 2;\n"
    assert f["previousContent"] == "export const v1 = 1;\n"
    assert f["beforeLabel"] == "Code revision 1"
    assert f["afterLabel"] == "Code revision 2"
    assert f["hasDiff"] is True
    assert f["contentChanged"] is True
    assert f["currentContentHash"]
    assert f["previousContentHash"]
    assert f["beforeContent"] == f["previousContent"]
    assert f["afterContent"] == f["currentContent"]
    assert f["additions"] >= 1
    assert f["unifiedDiff"]
    assert f["summary"]


def test_review_data_first_patch_falls_back_to_base_branch(db_session, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    base_text = (tmp_path / "playwright.config.ts").read_text(encoding="utf-8")

    from app.db.models.automation_job import AutomationJob

    job = AutomationJob(
        approved_case_id="review-base",
        requested_by="qa",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="awaiting_automation_review",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        coding_engine="stub",
        status="awaiting_review",
        current_round_number=1,
        created_by="qa",
    )
    db_session.add(sess)
    db_session.flush()
    rnd1 = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="qa",
        trigger_type="initial",
        status="completed",
    )
    db_session.add(rnd1)
    db_session.flush()

    pv1 = AutomationPatchVersion(
        automation_session_id=sess.id,
        revision_round_id=rnd1.id,
        version_number=1,
        patch_json=_patch_json(
            [{"path": "playwright.config.ts", "action": "modify", "content": "export default { patched: true };\n"}]
        ),
        is_current=True,
        created_by="qa",
    )
    db_session.add(pv1)
    db_session.commit()

    data = build_session_review_data_for_ui(db_session, sess.id)
    f = data["changedFiles"][0]
    assert f["path"] == "playwright.config.ts"
    assert f["previousContent"] == base_text
    assert "Base branch" in f["beforeLabel"]
    assert f["contentChanged"] is True


def test_review_conversation_timeline_shape(db_session, tmp_path: Path):
    sid, rnd1, rnd2 = _session_with_rounds(db_session, tmp_path)
    db_session.add(
        AutomationReviewRequest(
            automation_session_id=sid,
            revision_round_id=rnd2.id,
            actor_id="reviewer",
            instruction_text="Please fix assertions",
            target_scope="tests/login.spec.ts",
            action_type="request_revision",
            status="recorded",
        )
    )
    db_session.add(
        AutomationExecutionAttempt(
            automation_session_id=sid,
            revision_round_id=rnd2.id,
            attempt_number=2,
            target_test_file="tests/login.spec.ts",
            command_json=["npx", "playwright", "test"],
            result_json={"success": True, "message": "2 passed"},
            success=True,
        )
    )
    db_session.commit()

    data = build_session_review_data_for_ui(db_session, sid)
    conv = data["reviewConversation"]
    assert isinstance(conv, list)
    assert len(conv) >= 2
    types = {item["type"] for item in conv}
    assert "request_revision" in types
    assert "execution_result" in types
    rev = next(x for x in conv if x["type"] == "request_revision")
    assert rev["actor"] == "reviewer"
    assert rev["text"] == "Please fix assertions"
    assert rev["scope"] == "tests/login.spec.ts"
    assert rev["roundNumber"] == 2


def test_review_data_pr_info_exposed(db_session, tmp_path: Path):
    sid, rnd1, _rnd2 = _session_with_rounds(db_session, tmp_path)
    conn = RepositoryConnection(
        provider="github",
        display_name="T",
        owner_or_org="o",
        repo_name="r",
        default_branch="main",
        auth_type="github_pat_env",
        credential_reference="GITHUB_TOKEN",
        is_active=True,
        created_by="qa",
    )
    db_session.add(conn)
    db_session.flush()
    sess = db_session.get(AutomationSession, sid)
    assert sess is not None
    sess.status = "pr_created"
    job = db_session.get(AutomationJob, sess.automation_job_id)
    assert job is not None
    job.status = "pr_created"
    db_session.add(
        CodeReviewRequest(
            automation_session_id=sid,
            repository_connection_id=conn.id,
            provider="github",
            source_branch="qswarm/feature",
            target_branch="main",
            title="Automate login",
            body="PR body",
            external_id="42",
            external_url="https://github.com/o/r/pull/42",
            status="created",
            created_by="qa",
        )
    )
    db_session.commit()

    data = build_session_review_data_for_ui(db_session, sid)
    pr = data["prInfo"]
    assert pr is not None
    assert pr["externalUrl"] == "https://github.com/o/r/pull/42"
    assert pr["externalId"] == "42"
    assert pr["title"] == "Automate login"
    assert pr["sourceBranch"] == "qswarm/feature"
    assert pr["targetBranch"] == "main"
    assert pr["status"] == "created"
    assert "open_pr" in data["reviewSummary"]["nextActions"]

    conv = data["reviewConversation"]
    pr_events = [x for x in conv if x["type"] == "pr_created"]
    assert len(pr_events) == 1
    assert "Automate login" in pr_events[0]["text"]


def test_review_data_api_endpoint(ui_client, tmp_path, monkeypatch):
    from test_automation_jobs import _patch_playwright_run_for_job_and_review, _stub_execution_run_factory

    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    rc = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "owner": "rev-org",
            "repo": "rev-repo",
            "defaultBranch": "main",
            "authRef": "tok",
        },
    )
    conn_id = rc.json()["id"]
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "repositoryConnectionId": conn_id,
            "engine": "stub",
            "sourceRef": "REV-API",
            "createdBy": "qa",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["s"],
        },
    )
    sid = c.json()["id"]
    ui_client.post(f"/api/v1/sessions/{sid}/start", json={"actorId": "qa"})

    r = ui_client.get(f"/api/v1/sessions/{sid}/review-data")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "reviewSummary" in body
    assert "changedFiles" in body
    assert "reviewConversation" in body
    assert isinstance(body["changedFiles"], list)


def test_review_data_404(ui_client):
    r = ui_client.get(f"/api/v1/sessions/{uuid.uuid4()}/review-data")
    assert r.status_code == 404


def test_session_detail_unchanged_regression(ui_client, tmp_path, monkeypatch):
    from test_automation_jobs import _patch_playwright_run_for_job_and_review, _stub_execution_run_factory

    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "REG-1",
            "engine": "stub",
            "createdBy": "qa",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["x"],
        },
    )
    sid = c.json()["id"]
    det = ui_client.get(f"/api/v1/sessions/{sid}")
    assert det.status_code == 200
    body = det.json()
    assert "rounds" in body and "patches" in body
    assert "executions" in body and "reviews" in body
    assert "reviewSummary" not in body
