"""Sprint 2 session PR creation, repository connections, and source-control registry."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import get_settings
from app.core.constants import AutomationJobStatus, AutomationSessionStatus, SourceControlProviderName
from app.db.models.code_review_request import CodeReviewRequest
from app.source_control.errors import SourceControlAuthError, UnsupportedSourceControlProviderError
from app.source_control.github_provider_adapter import GitHubSourceControlAdapter
from app.source_control.registry import resolve_source_control_adapter
from sqlalchemy import select

from test_automation_jobs import _playwright_fixture_repo, _stub_execution_run_factory
from test_automation_sessions import _patch_playwright_run_for_job_and_review


def _repo_conn_body(**kw):
    base = {
        "provider": "github",
        "display_name": "Acme Web",
        "owner_or_org": "acme",
        "repo_name": "webapp",
        "created_by": "admin",
    }
    base.update(kw)
    return base


def test_repo_connection_crud(client, db_session):
    r = client.post("/repo-connections", json=_repo_conn_body())
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["provider"] == "github"

    lst = client.get("/repo-connections")
    assert lst.status_code == 200
    assert any(x["id"] == cid for x in lst.json()["items"])

    one = client.get(f"/repo-connections/{cid}")
    assert one.status_code == 200
    assert one.json()["repo_name"] == "webapp"

    pu = client.patch(f"/repo-connections/{cid}", json={"display_name": "Renamed"})
    assert pu.status_code == 200
    assert pu.json()["display_name"] == "Renamed"


def test_branch_policy_create_get_patch(client):
    r = client.post("/repo-connections", json=_repo_conn_body(display_name="P1"))
    cid = r.json()["id"]
    cr = client.post(
        f"/repo-connections/{cid}/branch-policy",
        json={"base_branch_default": "develop", "branch_naming_pattern": "feat/qswarm-{session_id}"},
    )
    assert cr.status_code == 201, cr.text
    assert cr.json()["base_branch_default"] == "develop"

    g = client.get(f"/repo-connections/{cid}/branch-policy")
    assert g.status_code == 200
    assert g.json()["branch_naming_pattern"] == "feat/qswarm-{session_id}"

    pa = client.patch(
        f"/repo-connections/{cid}/branch-policy",
        json={"allow_session_override": False},
    )
    assert pa.status_code == 200
    assert pa.json()["allow_session_override"] is False


def test_registry_resolves_github():
    ad = resolve_source_control_adapter("github")
    assert ad.provider_name == "github"


def test_registry_unknown_provider():
    with pytest.raises(UnsupportedSourceControlProviderError):
        resolve_source_control_adapter("unknown_x")


def test_create_repo_connection_unknown_provider(client):
    r = client.post(
        "/repo-connections",
        json={
            "provider": "not_a_real_provider",
            "display_name": "x",
            "owner_or_org": "o",
            "repo_name": "r",
            "created_by": "u",
        },
    )
    assert r.status_code == 400


def test_github_validate_config_missing_token(monkeypatch, db_session):
    from app.db.models.repository_connection import RepositoryConnection

    monkeypatch.setenv("GITHUB_TOKEN", "")
    get_settings.cache_clear()
    try:
        conn = RepositoryConnection(
            provider="github",
            display_name="t",
            owner_or_org="o",
            repo_name="r",
            default_branch="main",
            auth_type="github_pat_env",
            credential_reference="",
            is_active=True,
            created_by="u",
        )
        db_session.add(conn)
        db_session.flush()
        ad = GitHubSourceControlAdapter(get_settings())
        with pytest.raises(SourceControlAuthError):
            ad.validate_config(conn)
    finally:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        get_settings.cache_clear()


def _session_at_approved_for_pr(client, tmp_path, monkeypatch, **session_extra):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    body = {
        "approved_case_id": "SESS-PR-1",
        "created_by": "runner",
        "coding_engine": "stub",
        "repo_path": str(tmp_path.resolve()),
        "case_title": "Smoke",
        "steps": ["open"],
    }
    body.update(session_extra)
    r = client.post(
        "/automation/sessions",
        json=body,
    )
    sid = uuid.UUID(r.json()["id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    assert client.post(f"/automation/sessions/{sid}/approve", json={"actor_id": "qa"}).status_code == 200
    return sid


def test_create_pr_rejected_when_not_approved(client, tmp_path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "SESS-PR-BAD",
            "created_by": "runner",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "steps": ["s"],
        },
    )
    sid = r.json()["id"]
    conn = client.post("/repo-connections", json=_repo_conn_body(display_name="Conn"))
    cid = conn.json()["id"]
    pr = client.post(
        f"/automation/sessions/{sid}/create-pr",
        json={"actor_id": "a", "repository_connection_id": cid},
    )
    assert pr.status_code == 409


def test_create_pr_success_and_list_requests(client, tmp_path, monkeypatch, db_session):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_fake_token_for_validate")
    get_settings.cache_clear()
    sid = _session_at_approved_for_pr(client, tmp_path, monkeypatch)

    conn = client.post("/repo-connections", json=_repo_conn_body(display_name="GHConn"))
    assert conn.status_code == 201
    cid = conn.json()["id"]

    def _fake_pipeline(self, db, job, **kwargs):
        return {
            "pr_number": 77,
            "pr_url": "https://github.com/acme/webapp/pull/77",
            "commit_sha": "deadbeef",
            "source_branch": kwargs.get("source_branch"),
            "target_branch": kwargs.get("target_branch"),
            "refresh_notes": {},
        }

    monkeypatch.setattr(GitHubSourceControlAdapter, "run_session_pr_pipeline", _fake_pipeline)

    pr = client.post(
        f"/automation/sessions/{sid}/create-pr",
        json={"actor_id": "qa", "repository_connection_id": cid},
    )
    assert pr.status_code == 200, pr.text
    body = pr.json()
    assert body["job_status"] == AutomationJobStatus.PR_CREATED.value
    assert body["status"] == AutomationSessionStatus.PR_CREATED.value
    assert body["external_id"] == "77"
    assert "pull" in (body.get("external_url") or "").lower()

    lst = client.get(f"/automation/sessions/{sid}/code-review-requests")
    assert lst.status_code == 200
    items = lst.json()["items"]
    assert len(items) >= 1
    assert items[-1]["status"] == "created"

    rows = list(db_session.scalars(select(CodeReviewRequest).where(CodeReviewRequest.automation_session_id == sid)).all())
    assert len(rows) >= 1
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    get_settings.cache_clear()


def test_create_pr_failure_sets_pr_creation_failed(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_fake_token_for_validate")
    get_settings.cache_clear()
    sid = _session_at_approved_for_pr(client, tmp_path, monkeypatch)
    conn = client.post("/repo-connections", json=_repo_conn_body(display_name="GHFail"))
    cid = conn.json()["id"]

    def _boom(self, db, job, **kwargs):
        from app.source_control.errors import SourceControlPushError

        raise SourceControlPushError("push refused", code="source_control_push")

    monkeypatch.setattr(GitHubSourceControlAdapter, "run_session_pr_pipeline", _boom)
    pr = client.post(
        f"/automation/sessions/{sid}/create-pr",
        json={"actor_id": "qa", "repository_connection_id": cid},
    )
    assert pr.status_code == 502
    summ = client.get(f"/automation/sessions/{sid}").json()
    assert summ["job_status"] == AutomationJobStatus.PR_CREATION_FAILED.value
    assert summ["status"] == AutomationSessionStatus.PR_FAILED.value
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    get_settings.cache_clear()


def test_create_pr_renders_branch_policy_templates(client, tmp_path, monkeypatch, db_session):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_fake_token_for_validate")
    get_settings.cache_clear()
    sid = _session_at_approved_for_pr(
        client,
        tmp_path,
        monkeypatch,
        source_reference="jira/PROJ-99",
    )

    conn = client.post("/repo-connections", json=_repo_conn_body(display_name="TplConn"))
    assert conn.status_code == 201
    cid = conn.json()["id"]

    cr = client.post(
        f"/repo-connections/{cid}/branch-policy",
        json={
            "base_branch_default": "develop",
            "branch_naming_pattern": "qswarm/{session_id}",
            "pr_title_template": "PR {approved_case_id}",
            "pr_body_template": (
                "Automated by QSwarm.\n\n"
                "Session: {session_id}\nEngine: {coding_engine}\nSource: {source_reference}\n"
                "{source_branch} -> {target_branch}\nRepo {owner_or_org}/{repo_name}"
            ),
        },
    )
    assert cr.status_code == 201, cr.text

    captured: dict = {}

    def _fake_pipeline(self, db, job, **kwargs):
        captured.update(kwargs)
        return {
            "pr_number": 88,
            "pr_url": "https://github.com/acme/webapp/pull/88",
            "commit_sha": "cafe",
            "source_branch": kwargs.get("source_branch"),
            "target_branch": kwargs.get("target_branch"),
            "refresh_notes": {},
        }

    monkeypatch.setattr(GitHubSourceControlAdapter, "run_session_pr_pipeline", _fake_pipeline)

    pr = client.post(
        f"/automation/sessions/{sid}/create-pr",
        json={"actor_id": "qa", "repository_connection_id": cid},
    )
    assert pr.status_code == 200, pr.text
    assert captured.get("title") == "PR SESS-PR-1"
    body = captured.get("body") or ""
    assert "{session_id}" not in body
    assert "{coding_engine}" not in body
    assert "{source_reference}" not in body
    assert "Engine: stub" in body
    assert "jira/PROJ-99" in body
    assert str(sid) in body
    assert "acme/webapp" in body

    rows = list(db_session.scalars(select(CodeReviewRequest).where(CodeReviewRequest.automation_session_id == sid)).all())
    assert rows
    last = rows[-1]
    assert last.title == "PR SESS-PR-1"
    assert last.body == body
    assert "{" not in (last.body or "")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    get_settings.cache_clear()


def test_create_pr_unknown_template_placeholder_returns_400(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_fake_token_for_validate")
    get_settings.cache_clear()
    sid = _session_at_approved_for_pr(client, tmp_path, monkeypatch)

    conn = client.post("/repo-connections", json=_repo_conn_body(display_name="BadTpl"))
    cid = conn.json()["id"]
    cr = client.post(
        f"/repo-connections/{cid}/branch-policy",
        json={
            "pr_body_template": "prefix {not_a_supported_key} suffix",
        },
    )
    assert cr.status_code == 201, cr.text

    pr = client.post(
        f"/automation/sessions/{sid}/create-pr",
        json={"actor_id": "qa", "repository_connection_id": cid},
    )
    assert pr.status_code == 400, pr.text
    assert pr.json()["detail"]["code"] == "pr_template_invalid_placeholder"

    summ = client.get(f"/automation/sessions/{sid}").json()
    assert summ["job_status"] == AutomationJobStatus.APPROVED_FOR_PR.value

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    get_settings.cache_clear()


def test_source_control_provider_name_parse():
    assert SourceControlProviderName.parse("GITHUB") == SourceControlProviderName.GITHUB
