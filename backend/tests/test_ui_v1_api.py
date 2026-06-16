"""BFF /api/v1 UI contract (camelCase, aggregated shapes)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.db.session import get_db
from test_automation_jobs import (
    _ensure_git_repo_for_session_pr,
    _patch_playwright_run_for_job_and_review,
    _playwright_fixture_repo,
    _stub_execution_run_factory,
)


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


def test_ui_v1_dashboard_shape(ui_client):
    r = ui_client.get("/api/v1/dashboard")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "sessionStatusCounts" in j
    assert "recentSessions" in j
    assert "repositoryConnectionCount" in j
    assert "branchPolicyCount" in j
    assert "environment" in j
    assert "applicationName" in j


def test_ui_v1_settings_shape(ui_client):
    r = ui_client.get("/api/v1/settings")
    assert r.status_code == 200
    j = r.json()
    assert j.get("applicationName")
    assert "jira" in j
    assert "useStub" in j["jira"]


def test_ui_v1_repo_connections_crud(ui_client, db_session):
    r = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "displayName": "UI BFF",
            "ownerOrOrg": "acme",
            "repoName": "bff",
            "createdBy": "tester",
        },
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["displayName"] == "UI BFF"

    lst = ui_client.get("/api/v1/repo-connections")
    assert lst.status_code == 200
    assert "repoConnections" in lst.json()
    assert any(x["id"] == cid for x in lst.json()["repoConnections"])

    one = ui_client.get(f"/api/v1/repo-connections/{cid}")
    assert one.status_code == 200
    assert one.json()["repoName"] == "bff"

    pu = ui_client.patch(f"/api/v1/repo-connections/{cid}", json={"displayName": "Renamed BFF"})
    assert pu.status_code == 200
    assert pu.json()["displayName"] == "Renamed BFF"


def test_ui_v1_branch_policies_by_policy_id(ui_client, db_session):
    cr = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "displayName": "PolConn",
            "ownerOrOrg": "o",
            "repoName": "r",
            "createdBy": "u",
        },
    )
    conn_id = cr.json()["id"]
    pr = ui_client.post(
        "/api/v1/branch-policies",
        json={
            "repositoryConnectionId": conn_id,
            "baseBranchDefault": "develop",
            "branchNamingPattern": "feat/{session_id}",
        },
    )
    assert pr.status_code == 201, pr.text
    pid = pr.json()["id"]
    assert pr.json()["baseBranchDefault"] == "develop"

    lst = ui_client.get("/api/v1/branch-policies")
    assert lst.status_code == 200
    assert any(p["id"] == pid for p in lst.json()["branchPolicies"])

    g = ui_client.get(f"/api/v1/branch-policies/{pid}")
    assert g.status_code == 200

    pa = ui_client.patch(f"/api/v1/branch-policies/{pid}", json={"allowSessionOverride": False})
    assert pa.status_code == 200
    assert pa.json()["allowSessionOverride"] is False


def test_ui_v1_sessions_list_and_detail(ui_client, tmp_path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "UI-SESS-1",
            "createdBy": "runner",
            "codingEngine": "stub",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    assert c.status_code == 201, c.text
    sid = c.json()["id"]
    assert c.json()["approvedCaseId"] == "UI-SESS-1"

    lst = ui_client.get("/api/v1/sessions")
    assert lst.status_code == 200
    assert "sessions" in lst.json()
    assert any(s["id"] == sid for s in lst.json()["sessions"])

    det = ui_client.get(f"/api/v1/sessions/{sid}")
    assert det.status_code == 200
    body = det.json()
    assert "summary" in body and "rounds" in body
    assert "planVersions" in body and "patches" in body
    assert "executions" in body and "reviews" in body
    assert "codeReviewRequests" in body


def test_ui_v1_session_start_uses_legacy_flow(ui_client, tmp_path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "UI-START",
            "createdBy": "runner",
            "codingEngine": "stub",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["s"],
        },
    )
    sid = c.json()["id"]
    st = ui_client.post(f"/api/v1/sessions/{sid}/start", json={"actorId": "runner"})
    assert st.status_code == 200, st.text
    assert st.json().get("jobStatus") == "awaiting_automation_review"


def test_legacy_automation_sessions_still_works(client, tmp_path, monkeypatch):
    """Regression: internal route unchanged."""
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "LEG-1",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["x"],
        },
    )
    assert r.status_code == 201


def test_cors_includes_ui_origins():
    from app.main import app
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        r = client.options(
            "/api/v1/settings",
            headers={
                "Origin": "https://qswarm-ui.vercel.app",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.status_code in (200, 405)
        ac = r.headers.get("access-control-allow-origin")
        assert ac == "https://qswarm-ui.vercel.app" or ac == "*"

        r2 = client.options(
            "/api/v1/settings",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        ac2 = r2.headers.get("access-control-allow-origin")
        assert ac2 == "http://localhost:5173" or ac2 == "*"
