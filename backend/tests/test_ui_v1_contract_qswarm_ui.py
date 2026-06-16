"""
Contract tests: ``/api/v1`` JSON must match [Qswarm-UI](https://github.com/amiteshusf/Qswarm-UI)
``src/api/schemas.ts`` expectations (Zod). Run in CI on every change — failures mean the
BFF drifted from the stable product contract.

Marker: ``pytest -m contract`` (see ``pyproject.toml``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.db.session import get_db

pytestmark = pytest.mark.contract

SESSION_STATUS = frozenset(
    {"draft", "queued", "running", "awaiting_review", "revising", "succeeded", "failed", "cancelled"}
)
ROUND_STATUS = frozenset({"planned", "active", "complete", "failed"})
EXEC_STATUS = frozenset({"pending", "running", "passed", "failed", "skipped"})
REVIEW_STATUS = frozenset({"open", "addressed", "dismissed"})


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


def test_contract_settings_schema(ui_client):
    r = ui_client.get("/api/v1/settings")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j.keys()) >= {"engine", "infrastructure", "source"}
    assert set(j["engine"].keys()) >= {"defaultEngine", "maxRounds"}
    assert isinstance(j["engine"]["defaultEngine"], str)
    assert isinstance(j["engine"]["maxRounds"], int)
    assert isinstance(j["infrastructure"]["provider"], str)
    assert isinstance(j["source"]["system"], str)


def test_contract_branch_policies_array_and_shape(ui_client, db_session):
    cr = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "owner": "c-org",
            "repo": "c-repo",
            "defaultBranch": "main",
            "authRef": "tok",
        },
    )
    assert cr.status_code == 201, cr.text
    conn_id = cr.json()["id"]
    pr = ui_client.post(
        "/api/v1/branch-policies",
        json={
            "name": "default",
            "repositoryConnectionId": conn_id,
            "baseBranch": "develop",
            "branchPattern": "feat/{session_id}",
            "prTitleTemplate": "QSwarm {session_id}",
            "prBodyTemplate": "",
        },
    )
    assert pr.status_code == 201, pr.text
    pid = pr.json()["id"]
    keys = {"id", "name", "baseBranch", "branchPattern", "prTitleTemplate", "prBodyTemplate", "createdAt", "updatedAt"}
    assert keys <= set(pr.json().keys())
    assert pr.json()["repoConnectionId"] == conn_id

    lst = ui_client.get("/api/v1/branch-policies")
    assert lst.status_code == 200
    assert isinstance(lst.json(), list)
    for row in lst.json():
        assert keys <= set(row.keys())
        if "repoConnectionId" in row:
            assert isinstance(row["repoConnectionId"], str)

    one = ui_client.get(f"/api/v1/branch-policies/{pid}")
    assert one.status_code == 200
    assert keys <= set(one.json().keys())


def test_contract_sessions_list_and_detail_shape(ui_client, tmp_path, monkeypatch):
    from test_automation_jobs import (
        _ensure_git_repo_for_session_pr,
        _patch_playwright_run_for_job_and_review,
        _playwright_fixture_repo,
        _stub_execution_run_factory,
    )

    _playwright_fixture_repo(tmp_path)
    _ensure_git_repo_for_session_pr(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())

    rc = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "owner": "s-org",
            "repo": "s-repo",
            "defaultBranch": "main",
            "authRef": "tok2",
        },
    )
    assert rc.status_code == 201, rc.text
    conn_id = rc.json()["id"]

    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "repositoryConnectionId": conn_id,
            "engine": "stub",
            "sourceRef": "CONTRACT-1",
            "createdBy": "runner",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["open"],
        },
    )
    assert c.status_code == 201, c.text
    sid = c.json()["id"]
    detail = c.json()
    req = {
        "id",
        "status",
        "engine",
        "repoConnectionId",
        "sourceRef",
        "createdAt",
        "updatedAt",
        "rounds",
        "patches",
        "executions",
        "reviews",
    }
    assert req <= set(detail.keys())
    assert detail["status"] in SESSION_STATUS
    assert isinstance(detail["rounds"], list)

    lst = ui_client.get("/api/v1/sessions")
    assert lst.status_code == 200
    body = lst.json()
    assert isinstance(body, list)
    summ_keys = {"id", "status", "engine", "repoConnectionId", "sourceRef", "createdAt", "updatedAt"}
    for row in body:
        assert summ_keys <= set(row.keys())
        assert row["status"] in SESSION_STATUS

    det = ui_client.get(f"/api/v1/sessions/{sid}")
    assert det.status_code == 200
    d = det.json()
    assert req <= set(d.keys())
    for rnd in d["rounds"]:
        assert rnd["status"] in ROUND_STATUS
    for ex in d["executions"]:
        assert ex["status"] in EXEC_STATUS
    for rev in d["reviews"]:
        assert rev["status"] in REVIEW_STATUS
