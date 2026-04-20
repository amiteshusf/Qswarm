"""PR creation pipeline (monkeypatched git / GitHub / execution)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.constants import AuditEventType, AutomationJobStatus, PrRecordStatus
from app.connectors.github_pr import GitHubApiError
from app.db.models.audit_log import AuditLog
from app.db.models.automation_job import AutomationJob
from app.db.models.pr_record import PrRecord
from test_automation_jobs import (
    _patch_playwright_run_for_job_and_review,
    _playwright_fixture_repo,
    _stub_execution_run_factory,
)


class _FakeSettings:
    github_token = "ghp_test_token"
    github_default_repo_owner = "acme"
    github_default_repo_name = "webapp"
    github_api_base_url = "https://api.github.com"


def _job_at_approved_for_pr(client, tmp_path: Path, monkeypatch, *, case_id: str = "CASE-PR-OK") -> str:
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": case_id,
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "repo_owner": "acme",
            "repo_name": "webapp",
            "case_title": "Reset flow",
            "steps": ["open"],
        },
    )
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200
    ap = client.post(f"/automation/jobs/{jid}/approve", json={"actor_id": "qa.lead"})
    assert ap.status_code == 200
    assert ap.json()["status"] == AutomationJobStatus.APPROVED_FOR_PR.value
    return jid


@pytest.fixture(autouse=True)
def _patch_github_settings(monkeypatch):
    monkeypatch.setattr("app.services.pr_creation_service.get_settings", lambda: _FakeSettings())


def test_create_pr_success_persists_record_and_pr_created(client, tmp_path: Path, monkeypatch, db_session):
    jid = _job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-SUC")

    monkeypatch.setattr(
        "app.services.pr_creation_service.ensure_git_repo",
        lambda p: Path(p).resolve(),
    )
    monkeypatch.setattr("app.services.pr_creation_service.ensure_branch", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.pr_creation_service.fetch_base_branch",
        lambda *a, **k: {"fetched": True, "notes": []},
    )
    monkeypatch.setattr(
        "app.services.pr_creation_service.refresh_branch_from_base",
        lambda *a, **k: {
            "base_branch": "main",
            "updated": False,
            "conflicted": False,
            "conflict_files": [],
            "notes": ["already up to date"],
        },
    )
    monkeypatch.setattr("app.services.pr_creation_service.working_tree_has_changes", lambda r: True)
    monkeypatch.setattr("app.services.pr_creation_service.stage_all_changes", lambda r: None)
    monkeypatch.setattr("app.services.pr_creation_service.create_commit", lambda r, m: None)
    monkeypatch.setattr("app.services.pr_creation_service.get_head_sha", lambda r: "abc1234deadbeef")
    monkeypatch.setattr("app.services.pr_creation_service.push_branch", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.pr_creation_service.create_pull_request",
        lambda **kw: {"number": 99, "html_url": "https://github.com/acme/webapp/pull/99"},
    )

    resp = client.post(f"/automation/jobs/{jid}/create-pr", json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == AutomationJobStatus.PR_CREATED.value
    assert data["pr_number"] == 99
    assert "pull" in (data.get("pr_url") or "").lower()

    rows = db_session.execute(select(PrRecord).where(PrRecord.automation_job_id == uuid.UUID(jid))).scalars().all()
    assert len(rows) >= 1
    pr = rows[-1]
    assert pr.status == PrRecordStatus.PR_CREATED.value
    assert pr.commit_sha == "abc1234deadbeef"
    assert pr.branch_name.startswith("qswarm/")


def test_create_pr_wrong_state_409(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PR-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "repo_owner": "a",
            "repo_name": "b",
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/create-pr", json={}).status_code == 409


def test_create_pr_missing_prerequisites_400(client, tmp_path: Path, monkeypatch):
    """No GitHub token."""
    jid = _job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-PRE")

    class _NoTok(_FakeSettings):
        github_token = ""

    monkeypatch.setattr("app.services.pr_creation_service.get_settings", lambda: _NoTok())
    r = client.post(f"/automation/jobs/{jid}/create-pr", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "pr_prerequisites_missing"


def test_create_pr_base_refresh_conflict_human_input(client, tmp_path: Path, monkeypatch, db_session):
    jid = _job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-CFL")

    monkeypatch.setattr(
        "app.services.pr_creation_service.ensure_git_repo",
        lambda p: Path(p).resolve(),
    )
    monkeypatch.setattr("app.services.pr_creation_service.ensure_branch", lambda *a, **k: None)
    monkeypatch.setattr("app.services.pr_creation_service.fetch_base_branch", lambda *a, **k: {"fetched": True})
    monkeypatch.setattr(
        "app.services.pr_creation_service.refresh_branch_from_base",
        lambda *a, **k: {
            "base_branch": "main",
            "updated": False,
            "conflicted": True,
            "conflict_files": ["tests/x.spec.ts"],
            "notes": ["merge conflict"],
        },
    )
    monkeypatch.setattr("app.services.pr_creation_service.abort_merge_if_in_progress", lambda r: None)

    resp = client.post(f"/automation/jobs/{jid}/create-pr", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == AutomationJobStatus.AWAITING_HUMAN_INPUT.value
    assert "conflict" in resp.json()["message"].lower()

    pr = db_session.execute(select(PrRecord).where(PrRecord.automation_job_id == uuid.UUID(jid))).scalars().first()
    assert pr.status == PrRecordStatus.BASE_REFRESH_CONFLICT.value

    audits = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    assert AuditEventType.AUTOMATION_BASE_REFRESH_CONFLICT.value in {x.event_type for x in audits}


def test_create_pr_execution_fails_after_refresh_no_pr(client, tmp_path: Path, monkeypatch, db_session):
    jid = _job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-EX")

    monkeypatch.setattr(
        "app.services.pr_creation_service.ensure_git_repo",
        lambda p: Path(p).resolve(),
    )
    monkeypatch.setattr("app.services.pr_creation_service.ensure_branch", lambda *a, **k: None)
    monkeypatch.setattr("app.services.pr_creation_service.fetch_base_branch", lambda *a, **k: {"fetched": True})
    monkeypatch.setattr(
        "app.services.pr_creation_service.refresh_branch_from_base",
        lambda *a, **k: {
            "base_branch": "main",
            "updated": True,
            "conflicted": False,
            "conflict_files": [],
            "notes": ["merged"],
        },
    )
    monkeypatch.setattr(
        "app.services.pr_creation_service.run_playwright_execution_for_job",
        lambda *a, **k: {
            "framework_type": "playwright",
            "command": [],
            "target_test_file": "tests/smoke.spec.ts",
            "success": False,
            "exit_code": 1,
            "duration_ms": 1,
            "stdout_tail": "",
            "stderr_tail": "expect(locator).toBeVisible() failed",
            "artifact_paths": [],
            "notes": [],
        },
    )

    resp = client.post(f"/automation/jobs/{jid}/create-pr", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == AutomationJobStatus.FAILED.value
    pr = db_session.execute(select(PrRecord).where(PrRecord.automation_job_id == uuid.UUID(jid))).scalars().first()
    assert pr.status == PrRecordStatus.FAILED.value


def test_create_pr_git_workspace_error_failed(client, tmp_path: Path, monkeypatch):
    from app.services.git_workspace_service import GitWorkspaceError

    jid = _job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-GIT")

    monkeypatch.setattr(
        "app.services.pr_creation_service.ensure_git_repo",
        lambda p: (_ for _ in ()).throw(GitWorkspaceError("not a git repository")),
    )

    resp = client.post(f"/automation/jobs/{jid}/create-pr", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == AutomationJobStatus.FAILED.value


def test_create_pr_github_api_error_failed(client, tmp_path: Path, monkeypatch):
    jid = _job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-GH")

    monkeypatch.setattr(
        "app.services.pr_creation_service.ensure_git_repo",
        lambda p: Path(p).resolve(),
    )
    monkeypatch.setattr("app.services.pr_creation_service.ensure_branch", lambda *a, **k: None)
    monkeypatch.setattr("app.services.pr_creation_service.fetch_base_branch", lambda *a, **k: {"fetched": True})
    monkeypatch.setattr(
        "app.services.pr_creation_service.refresh_branch_from_base",
        lambda *a, **k: {"base_branch": "main", "updated": False, "conflicted": False, "conflict_files": [], "notes": []},
    )
    monkeypatch.setattr("app.services.pr_creation_service.working_tree_has_changes", lambda r: True)
    monkeypatch.setattr("app.services.pr_creation_service.stage_all_changes", lambda r: None)
    monkeypatch.setattr("app.services.pr_creation_service.create_commit", lambda r, m: None)
    monkeypatch.setattr("app.services.pr_creation_service.get_head_sha", lambda r: "sha1")
    monkeypatch.setattr("app.services.pr_creation_service.push_branch", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.pr_creation_service.create_pull_request",
        lambda **kw: (_ for _ in ()).throw(GitHubApiError("rate limited", status_code=403)),
    )

    resp = client.post(f"/automation/jobs/{jid}/create-pr", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == AutomationJobStatus.FAILED.value


def test_create_pr_audit_events_on_success(client, tmp_path: Path, monkeypatch, db_session):
    jid = uuid.UUID(_job_at_approved_for_pr(client, tmp_path, monkeypatch, case_id="CASE-PR-AUD"))

    monkeypatch.setattr(
        "app.services.pr_creation_service.ensure_git_repo",
        lambda p: Path(p).resolve(),
    )
    monkeypatch.setattr("app.services.pr_creation_service.ensure_branch", lambda *a, **k: None)
    monkeypatch.setattr("app.services.pr_creation_service.fetch_base_branch", lambda *a, **k: {"fetched": True})
    monkeypatch.setattr(
        "app.services.pr_creation_service.refresh_branch_from_base",
        lambda *a, **k: {"base_branch": "main", "updated": False, "conflicted": False, "conflict_files": [], "notes": []},
    )
    monkeypatch.setattr("app.services.pr_creation_service.working_tree_has_changes", lambda r: True)
    monkeypatch.setattr("app.services.pr_creation_service.stage_all_changes", lambda r: None)
    monkeypatch.setattr("app.services.pr_creation_service.create_commit", lambda r, m: None)
    monkeypatch.setattr("app.services.pr_creation_service.get_head_sha", lambda r: "sha2")
    monkeypatch.setattr("app.services.pr_creation_service.push_branch", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.pr_creation_service.create_pull_request",
        lambda **kw: {"number": 1, "html_url": "https://example.com/1"},
    )

    assert client.post(f"/automation/jobs/{jid}/create-pr", json={}).status_code == 200

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_PR_CREATION_STARTED.value in types
    assert AuditEventType.AUTOMATION_BASE_REFRESH_COMPLETED.value in types
    assert AuditEventType.AUTOMATION_COMMIT_CREATED.value in types
    assert AuditEventType.AUTOMATION_PR_CREATED.value in types


def test_git_workspace_job_branch_name_shape():
    from app.services.git_workspace_service import job_branch_name

    b = job_branch_name("CASE-99/reset", uuid.UUID("12345678-1234-5678-1234-567812345678"))
    assert b.startswith("qswarm/")
    assert "12345678" in b
