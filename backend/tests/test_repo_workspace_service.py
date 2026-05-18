"""Tests for hosted workspace materialization (repo_workspace_service)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_connection import RepositoryConnection
from app.services import repo_workspace_service as rws
from app.services.framework_scan_service import FrameworkScanError
from app.services.repo_workspace_service import (
    RepoAuthError,
    RepoCheckoutError,
    RepoCloneError,
    RepoWorkspacePreparationError,
    prepare_automation_session_workspace,
    session_repo_workspace_path,
)


def test_session_repo_workspace_path_deterministic():
    sid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    p = session_repo_workspace_path(sid, "/tmp/qswarm")
    assert str(p) == f"/tmp/qswarm/sessions/{sid}/repo"


def test_existing_repo_path_local_mode(db_session, tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "marker").write_text("x")
    job = AutomationJob(
        approved_case_id="c1",
        requested_by="u",
        repo_path=str(root),
        base_branch="main",
    )
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=str(root),
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    settings = Settings()
    res = prepare_automation_session_workspace(
        db_session, session=sess, job=job, settings=settings
    )
    assert res.mode == "existing_path"
    assert Path(res.workspace_path) == root.resolve()
    assert job.repo_path == str(root.resolve())


def test_missing_repo_path_with_connection_sets_paths(monkeypatch, db_session, tmp_path: Path):
    conn = RepositoryConnection(
        provider="github",
        display_name="Test",
        owner_or_org="acme",
        repo_name="demo",
        clone_url=None,
        default_branch="develop",
        created_by="u",
    )
    db_session.add(conn)
    db_session.flush()

    job = AutomationJob(
        approved_case_id="c1",
        requested_by="u",
        repo_path=None,
        repo_owner=None,
        repo_name=None,
        base_branch="feature-x",
        status="pending",
    )
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=None,
        repository_connection_id=conn.id,
        base_branch="feature-x",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    target = tmp_path / "managed" / "sessions" / str(sess.id) / "repo"

    def fake_run(argv, **kwargs):
        cp = MagicMock()
        cp.returncode = 0
        cp.stderr = ""
        cp.stdout = ""
        if argv[:2] == ["git", "clone"]:
            dest = Path(argv[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            (dest / "README.md").write_text("# ok")
        return cp

    monkeypatch.setattr(rws, "_run_git", lambda cwd, args, timeout=30, git_bin="git": fake_run(["git", *args]))
    monkeypatch.setattr(rws.subprocess, "run", fake_run)

    settings = Settings(
        qswarm_workspace_root=str(tmp_path / "managed"),
        github_token="fake-token-for-tests",
    )
    res = prepare_automation_session_workspace(
        db_session, session=sess, job=job, settings=settings
    )
    assert res.mode == "cloned_workspace"
    assert res.target_branch == "feature-x"
    assert Path(res.workspace_path).resolve() == target.resolve()
    assert job.repo_path == str(target.resolve())
    assert sess.repo_path == str(target.resolve())
    assert res.clone_url_used == "https://github.com/acme/demo.git"
    assert "x-access-token" not in (res.clone_url_used or "")


def test_branch_fallback_connection_default(db_session, tmp_path: Path, monkeypatch):
    conn = RepositoryConnection(
        provider="github",
        display_name="Test",
        owner_or_org="o",
        repo_name="r",
        default_branch="develop",
        created_by="u",
    )
    db_session.add(conn)
    db_session.flush()
    job = AutomationJob(approved_case_id="c", requested_by="u", repo_path=None, base_branch="main", status="pending")
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=None,
        repository_connection_id=conn.id,
        base_branch="",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    checkout_args: list[str] = []

    def fake_run(argv, **kwargs):
        cp = MagicMock()
        cp.returncode = 0
        cp.stderr = ""
        cp.stdout = ""
        if argv[:2] == ["git", "clone"]:
            dest = Path(argv[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
        if len(argv) >= 2 and argv[0] == "git" and "checkout" in argv:
            checkout_args.extend(argv)
        return cp

    monkeypatch.setattr(rws, "_run_git", lambda cwd, args, timeout=30, git_bin="git": fake_run(["git", *args]))
    monkeypatch.setattr(rws.subprocess, "run", fake_run)

    settings = Settings(qswarm_workspace_root=str(tmp_path / "w"), github_token="tok")
    prepare_automation_session_workspace(db_session, session=sess, job=job, settings=settings)
    assert any("develop" in str(x) for x in checkout_args)


def test_clone_failure_maps_to_repo_clone_error(db_session, tmp_path: Path, monkeypatch):
    conn = RepositoryConnection(
        provider="github",
        display_name="T",
        owner_or_org="o",
        repo_name="r",
        created_by="u",
    )
    db_session.add(conn)
    db_session.flush()
    job = AutomationJob(approved_case_id="c", requested_by="u", repo_path=None, status="pending")
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=None,
        repository_connection_id=conn.id,
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    def boom(argv, **kwargs):
        cp = MagicMock()
        if argv[:2] == ["git", "clone"]:
            cp.returncode = 1
            cp.stderr = "fatal: https://x-access-token:SECRET@github.com/o/r.git not found"
            cp.stdout = ""
            return cp
        cp.returncode = 0
        cp.stderr = ""
        cp.stdout = ""
        return cp

    monkeypatch.setattr(rws.subprocess, "run", boom)
    monkeypatch.setattr(
        rws,
        "_run_git",
        lambda cwd, args, timeout=30, git_bin="git": boom(["git", *args]),
    )

    settings = Settings(qswarm_workspace_root=str(tmp_path / "w"), github_token="SECRET")
    with pytest.raises(RepoCloneError) as ei:
        prepare_automation_session_workspace(db_session, session=sess, job=job, settings=settings)
    assert ei.value.code == "repo_clone_failed"
    assert "SECRET" not in ei.value.message


def test_auth_missing_raises_repo_auth(db_session, tmp_path: Path, monkeypatch):
    conn = RepositoryConnection(
        provider="github",
        display_name="T",
        owner_or_org="o",
        repo_name="r",
        created_by="u",
    )
    db_session.add(conn)
    db_session.flush()
    job = AutomationJob(approved_case_id="c", requested_by="u", repo_path=None, status="pending")
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=None,
        repository_connection_id=conn.id,
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    settings = Settings(github_token="")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(RepoAuthError) as ei:
        prepare_automation_session_workspace(db_session, session=sess, job=job, settings=settings)
    assert ei.value.code == "repo_auth_required"


def test_invalid_declared_path_and_no_clone_source_framework_scan(db_session, tmp_path: Path):
    missing = tmp_path / "nope"
    job = AutomationJob(
        approved_case_id="c",
        requested_by="u",
        repo_path=str(missing),
        status="pending",
    )
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=str(missing),
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    with pytest.raises(FrameworkScanError) as ei:
        prepare_automation_session_workspace(
            db_session, session=sess, job=job, settings=Settings(github_token="x")
        )
    assert ei.value.code == "repo_path_not_found"


def test_checkout_failure(monkeypatch, db_session, tmp_path: Path):
    conn = RepositoryConnection(
        provider="github",
        display_name="T",
        owner_or_org="o",
        repo_name="r",
        created_by="u",
    )
    db_session.add(conn)
    db_session.flush()
    job = AutomationJob(approved_case_id="c", requested_by="u", repo_path=None, status="pending")
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=None,
        repository_connection_id=conn.id,
        base_branch="missing-branch-xyz",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    def fake_run(argv, **kwargs):
        cp = MagicMock()
        cp.stderr = ""
        cp.stdout = ""
        if argv[:2] == ["git", "clone"]:
            cp.returncode = 0
            dest = Path(argv[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
            return cp
        if argv[:2] == ["git", "fetch"]:
            cp.returncode = 0
            return cp
        if "checkout" in argv:
            cp.returncode = 1
            cp.stderr = "error: pathspec 'missing-branch-xyz' did not match any file(s) known to git"
            return cp
        cp.returncode = 0
        return cp

    monkeypatch.setattr(rws, "_run_git", lambda cwd, args, timeout=30, git_bin="git": fake_run(["git", *args]))
    monkeypatch.setattr(rws.subprocess, "run", fake_run)

    settings = Settings(qswarm_workspace_root=str(tmp_path / "w"), github_token="tok")
    with pytest.raises(RepoCheckoutError):
        prepare_automation_session_workspace(db_session, session=sess, job=job, settings=settings)


def test_repository_connection_not_found(db_session, tmp_path: Path):
    job = AutomationJob(approved_case_id="c", requested_by="u", repo_path=None, status="pending")
    sess = AutomationSession(
        automation_job_id=None,
        repo_path=None,
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        created_by="u",
    )
    db_session.add(job)
    db_session.flush()
    sess.automation_job_id = job.id
    db_session.add(sess)
    db_session.flush()

    bad = uuid.uuid4()
    with pytest.raises(RepoWorkspacePreparationError) as ei:
        prepare_automation_session_workspace(
            db_session, session=sess, job=job, repository_connection_id=bad, settings=Settings(github_token="t")
        )
    assert ei.value.code == "repository_connection_not_found"
