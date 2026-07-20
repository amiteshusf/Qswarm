"""Tests for GET /api/v1/sessions/{id}/brief (session brief / plan preview payload)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_plan_version import AutomationPlanVersion
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.db.models.repository_connection import RepositoryConnection
from app.db.session import get_db
from app.main import app
from app.services.ui_v1_session_brief_service import build_session_brief_for_ui


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


def _session_with_job(db_session, tmp_path: Path) -> tuple[uuid.UUID, AutomationJob, AutomationRevisionRound]:
    job = AutomationJob(
        approved_case_id="CASE-42",
        requested_by="qa",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="pending",
        case_spec_json={
            "title": "Login smoke test",
            "description": "Verify user can log in",
            "steps": ["Open login page", "Submit credentials"],
            "expected_results": ["Dashboard visible"],
        },
        change_plan_json={
            "framework_type": "playwright",
            "target_test_file": "tests/login.spec.ts",
            "files_to_modify": ["tests/login.spec.ts"],
            "rationale": "Add login coverage for smoke suite",
        },
        framework_summary_json={"framework_type": "playwright", "test_root": "tests"},
        repo_context_json={"similar_test_files": ["tests/home.spec.ts"]},
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        coding_engine="stub",
        status="draft",
        current_round_number=0,
        created_by="qa",
        source_system="jira",
        source_reference="PROJ-123",
    )
    db_session.add(sess)
    db_session.flush()
    rnd = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="qa",
        trigger_type="initial",
        status="pending",
    )
    db_session.add(rnd)
    db_session.flush()
    return sess.id, job, rnd


def test_session_brief_source_summary_when_available(db_session, tmp_path: Path):
    sid, _job, _rnd = _session_with_job(db_session, tmp_path)
    db_session.commit()

    data = build_session_brief_for_ui(db_session, sid)
    src = data["sourceSummary"]
    assert src["sourceSystem"] == "jira"
    assert src["sourceReference"] == "PROJ-123"
    assert src["caseId"] == "CASE-42"
    assert src["sourceTitle"] == "Login smoke test"
    assert "Open login page" in src["steps"]
    assert "Dashboard visible" in src["expectedResults"]


def test_session_brief_automation_plan_preview(db_session, tmp_path: Path):
    sid, job, rnd = _session_with_job(db_session, tmp_path)
    db_session.add(
        AutomationPlanVersion(
            automation_session_id=sid,
            revision_round_id=rnd.id,
            version_number=1,
            plan_json={
                "framework_type": "playwright",
                "target_test_file": "tests/login.spec.ts",
                "files_to_modify": ["tests/login.spec.ts", "pages/login.page.ts"],
                "rationale": "Versioned plan rationale",
            },
            is_current=True,
            created_by="qa",
        )
    )
    db_session.commit()

    data = build_session_brief_for_ui(db_session, sid)
    brief = data["automationBrief"]
    assert brief["available"] is True
    assert brief["frameworkType"] == "playwright"
    assert brief["targetTestFile"] == "tests/login.spec.ts"
    assert "tests/login.spec.ts" in brief["filesToModify"]
    assert brief["rationale"] == "Versioned plan rationale"
    assert brief["planVersion"] == 1
    assert brief["frameworkSummary"]["testRoot"] == "tests"
    assert brief["repoContextSummary"]["similarTestFiles"] == ["tests/home.spec.ts"]


def test_session_brief_setup_includes_repo_and_engine(db_session, tmp_path: Path):
    sid, _job, _rnd = _session_with_job(db_session, tmp_path)
    conn = RepositoryConnection(
        provider="github",
        display_name="Main repo",
        owner_or_org="acme",
        repo_name="web-app",
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
    sess.repository_connection_id = conn.id
    sess.repo_owner = "acme"
    sess.repo_name = "web-app"
    db_session.add(
        RepositoryBranchPolicy(
            repository_connection_id=conn.id,
            base_branch_default="main",
            branch_naming_pattern="qswarm/{session_id}",
        )
    )
    db_session.commit()

    data = build_session_brief_for_ui(db_session, sid)
    setup = data["setup"]
    assert setup["engine"] == "stub"
    assert setup["repository"]["owner"] == "acme"
    assert setup["repository"]["name"] == "web-app"
    assert setup["repository"]["displayName"] == "Main repo"
    assert setup["branchPolicy"] is not None
    assert data["sessionState"]["status"]


def test_session_brief_api_endpoint(ui_client, tmp_path):
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "BRIEF-1",
            "engine": "stub",
            "createdBy": "qa",
            "repoPath": str(tmp_path.resolve()),
            "sourceSystem": "testrail",
            "sourceRef": "TR-99",
            "steps": ["Do thing"],
        },
    )
    assert c.status_code == 201, c.text
    sid = c.json()["id"]

    r = ui_client.get(f"/api/v1/sessions/{sid}/brief")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sessionId"] == sid
    assert "sourceSummary" in body
    assert "setup" in body
    assert "automationBrief" in body
    assert "sessionState" in body


def test_session_brief_404(ui_client):
    r = ui_client.get(f"/api/v1/sessions/{uuid.uuid4()}/brief")
    assert r.status_code == 404


def test_session_detail_unchanged_by_brief_endpoint(ui_client, tmp_path):
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "REG-BRIEF",
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
    assert "sourceSummary" not in body
    assert "automationBrief" not in body
