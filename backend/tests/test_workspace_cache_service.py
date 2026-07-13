"""Workspace cache entries and ``ensure_pr_workspace_ready`` (hosted create-pr durability)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_patch_version import AutomationPatchVersion
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_connection import RepositoryConnection
from app.db.models.workspace_cache_entry import WorkspaceCacheEntry
from app.services.repo_workspace_service import RepoWorkspacePreparationError, WorkspacePreparationResult
from app.services import workspace_cache_service as wcs
from app.source_control.errors import SourceControlConfigurationError, SourceControlRepoError
from test_automation_jobs import _ensure_git_repo_for_session_pr, _playwright_fixture_repo


def _repository_connection(db_session) -> RepositoryConnection:
    c = RepositoryConnection(
        provider="github",
        display_name="T",
        owner_or_org="o",
        repo_name="r",
        default_branch="main",
        auth_type="github_pat_env",
        credential_reference="",
        is_active=True,
        created_by="u",
    )
    db_session.add(c)
    db_session.flush()
    return c


def _git_fixture(root: Path) -> None:
    _playwright_fixture_repo(root)
    _ensure_git_repo_for_session_pr(root)


def _session_job_round(
    db_session,
    *,
    repo_path: str | None,
) -> tuple[AutomationSession, AutomationJob, AutomationRevisionRound]:
    job = AutomationJob(
        approved_case_id=f"wcache-{uuid.uuid4().hex[:8]}",
        requested_by="u",
        repo_path=repo_path,
        base_branch="main",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        repo_path=repo_path,
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=1,
        created_by="u",
    )
    db_session.add(sess)
    db_session.flush()
    rnd = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="u",
        trigger_type="initial",
        status="completed",
    )
    db_session.add(rnd)
    db_session.flush()
    return sess, job, rnd


def _add_current_patch(
    db_session,
    sess: AutomationSession,
    rnd: AutomationRevisionRound,
    *,
    new_content: str = "// patched for pr\n",
) -> AutomationPatchVersion:
    pjson = {
        "framework_type": "playwright",
        "target_test_file": "tests/smoke.spec.ts",
        "generated_files": [
            {"path": "tests/smoke.spec.ts", "action": "modify", "content": new_content},
        ],
    }
    pv = AutomationPatchVersion(
        automation_session_id=sess.id,
        revision_round_id=rnd.id,
        version_number=1,
        patch_json=pjson,
        is_current=True,
        created_by="u",
    )
    db_session.add(pv)
    db_session.flush()
    return pv


def test_ensure_pr_reuses_existing_git_workspace(db_session, tmp_path, monkeypatch):
    root = tmp_path / "reuse"
    root.mkdir()
    _git_fixture(root)
    conn = _repository_connection(db_session)
    sess, job, rnd = _session_job_round(db_session, repo_path=str(root.resolve()))
    _add_current_patch(db_session, sess, rnd)

    def _no_prepare(*_a, **_k):
        raise AssertionError("prepare_automation_session_workspace should not run on reuse")

    monkeypatch.setattr(
        "app.services.workspace_cache_service.prepare_automation_session_workspace",
        _no_prepare,
    )
    out = wcs.ensure_pr_workspace_ready(
        db_session,
        session=sess,
        job=job,
        repository_connection_id=conn.id,
        settings=Settings(qswarm_workspace_cache_ttl_minutes=60),
    )
    assert out.repo_path == str(root.resolve())
    assert len(out.patch_files) == 1
    rows = list(
        db_session.scalars(
            select(WorkspaceCacheEntry).where(WorkspaceCacheEntry.automation_session_id == sess.id)
        ).all()
    )
    assert any(r.status == wcs.WORKSPACE_CACHE_STATUS_ACTIVE for r in rows)


def test_ensure_pr_rebuilds_when_repo_path_missing_and_reapplies_patch(db_session, tmp_path, monkeypatch):
    conn = _repository_connection(db_session)
    sess, job, rnd = _session_job_round(db_session, repo_path="/no/such/path/repo")
    _add_current_patch(db_session, sess, rnd, new_content="// rebuilt workspace patch\n")

    managed = tmp_path / "managed" / str(sess.id) / "repo"

    def fake_prepare(db, *, session, job, repository_connection_id=None, settings=None):
        managed.mkdir(parents=True, exist_ok=True)
        _git_fixture(managed)
        job.repo_path = str(managed.resolve())
        session.repo_path = str(managed.resolve())
        db.flush()
        return WorkspacePreparationResult(
            mode="cloned_workspace",
            workspace_path=str(managed.resolve()),
            clone_url_used="https://github.com/o/r.git",
            provider="github",
            target_branch="main",
            source_reference=None,
            notes="test",
        )

    monkeypatch.setattr(
        "app.services.workspace_cache_service.prepare_automation_session_workspace",
        fake_prepare,
    )
    out = wcs.ensure_pr_workspace_ready(
        db_session,
        session=sess,
        job=job,
        repository_connection_id=conn.id,
        settings=Settings(qswarm_workspace_cache_ttl_minutes=60),
    )
    assert out.repo_path == str(managed.resolve())
    assert (managed / "tests" / "smoke.spec.ts").read_text() == "// rebuilt workspace patch\n"
    active = db_session.scalar(
        select(WorkspaceCacheEntry).where(
            WorkspaceCacheEntry.automation_session_id == sess.id,
            WorkspaceCacheEntry.status == wcs.WORKSPACE_CACHE_STATUS_ACTIVE,
        )
    )
    assert active is not None
    assert active.workspace_path == str(managed.resolve())
    assert active.repository_connection_id == conn.id


def test_ensure_pr_rebuilds_when_path_not_git(db_session, tmp_path, monkeypatch):
    bad = tmp_path / "not_git"
    bad.mkdir()
    (bad / "README.md").write_text("x")
    conn = _repository_connection(db_session)
    sess, job, rnd = _session_job_round(db_session, repo_path=str(bad.resolve()))
    _add_current_patch(db_session, sess, rnd)

    managed = tmp_path / "managed2" / str(sess.id) / "repo"

    def fake_prepare(db, *, session, job, repository_connection_id=None, settings=None):
        managed.mkdir(parents=True, exist_ok=True)
        _git_fixture(managed)
        job.repo_path = str(managed.resolve())
        session.repo_path = str(managed.resolve())
        db.flush()
        return WorkspacePreparationResult(
            mode="cloned_workspace",
            workspace_path=str(managed.resolve()),
            clone_url_used="https://github.com/o/r.git",
            provider="github",
            target_branch="main",
            source_reference=None,
            notes="test",
        )

    monkeypatch.setattr(
        "app.services.workspace_cache_service.prepare_automation_session_workspace",
        fake_prepare,
    )
    out = wcs.ensure_pr_workspace_ready(
        db_session,
        session=sess,
        job=job,
        repository_connection_id=conn.id,
        settings=Settings(),
    )
    assert out.repo_path == str(managed.resolve())


def test_ensure_pr_fails_clearly_without_current_patch(db_session, tmp_path):
    root = tmp_path / "ok"
    root.mkdir()
    _git_fixture(root)
    conn = _repository_connection(db_session)
    sess, job, rnd = _session_job_round(db_session, repo_path="/totally/missing/repo")
    # no AutomationPatchVersion
    with pytest.raises(SourceControlConfigurationError) as ei:
        wcs.ensure_pr_workspace_ready(
            db_session,
            session=sess,
            job=job,
            repository_connection_id=conn.id,
            settings=Settings(),
        )
    assert ei.value.code == "pr_no_current_patch"


def test_ensure_pr_fails_clearly_when_patch_has_no_generated_files(db_session, tmp_path, monkeypatch):
    conn = _repository_connection(db_session)
    sess, job, rnd = _session_job_round(db_session, repo_path="/missing/path")
    pv = AutomationPatchVersion(
        automation_session_id=sess.id,
        revision_round_id=rnd.id,
        version_number=1,
        patch_json={"framework_type": "playwright", "target_test_file": "tests/smoke.spec.ts", "generated_files": []},
        is_current=True,
        created_by="u",
    )
    db_session.add(pv)
    db_session.flush()

    def fake_prepare(db, *, session, job, repository_connection_id=None, settings=None):
        root = tmp_path / "m3" / str(session.id) / "repo"
        root.mkdir(parents=True, exist_ok=True)
        _git_fixture(root)
        job.repo_path = str(root.resolve())
        session.repo_path = str(root.resolve())
        db.flush()
        return WorkspacePreparationResult(
            mode="cloned_workspace",
            workspace_path=str(root.resolve()),
            clone_url_used=None,
            provider="github",
            target_branch="main",
            source_reference=None,
            notes="t",
        )

    monkeypatch.setattr(
        "app.services.workspace_cache_service.prepare_automation_session_workspace",
        fake_prepare,
    )
    with pytest.raises(SourceControlConfigurationError) as ei:
        wcs.ensure_pr_workspace_ready(
            db_session,
            session=sess,
            job=job,
            repository_connection_id=conn.id,
            settings=Settings(),
        )
    assert ei.value.code == "pr_no_current_patch"


def test_ensure_prepare_failure_maps_to_repo_error(db_session, tmp_path, monkeypatch):
    conn = _repository_connection(db_session)
    sess, job, rnd = _session_job_round(db_session, repo_path="/missing/path")
    _add_current_patch(db_session, sess, rnd)

    def boom(*_a, **_k):
        raise RepoWorkspacePreparationError("connection not usable", code="repository_connection_not_found")

    monkeypatch.setattr(
        "app.services.workspace_cache_service.prepare_automation_session_workspace",
        boom,
    )
    with pytest.raises(SourceControlRepoError) as ei:
        wcs.ensure_pr_workspace_ready(
            db_session,
            session=sess,
            job=job,
            repository_connection_id=conn.id,
            settings=Settings(),
        )
    assert ei.value.code == "repository_connection_not_found"


def test_expire_due_workspace_cache_entries_marks_expired(db_session, tmp_path):
    from datetime import datetime, timedelta, timezone

    conn = _repository_connection(db_session)
    sess, job, _rnd = _session_job_round(db_session, repo_path=str(tmp_path))
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    row = WorkspaceCacheEntry(
        automation_session_id=sess.id,
        repository_connection_id=conn.id,
        workspace_path="/tmp/x",
        status=wcs.WORKSPACE_CACHE_STATUS_ACTIVE,
        last_used_at=past,
        expires_at=past + timedelta(minutes=1),
    )
    db_session.add(row)
    db_session.flush()
    n = wcs.expire_due_workspace_cache_entries(db_session)
    assert n >= 1
    db_session.refresh(row)
    assert row.status == wcs.WORKSPACE_CACHE_STATUS_EXPIRED
