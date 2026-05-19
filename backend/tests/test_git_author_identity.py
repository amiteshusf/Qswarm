"""Repo-local git author identity for PR commits (QSWARM_GIT_AUTHOR_*)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.git_workspace_service import (
    ensure_git_author_identity,
    git_author_from_settings,
)


def test_git_author_from_settings_missing_raises():
    s = Settings(qswarm_git_author_name="", qswarm_git_author_email="")
    with pytest.raises(ValueError, match="pr_git_author_not_configured"):
        git_author_from_settings(s)


def test_git_author_from_settings_invalid_email_raises():
    s = Settings(qswarm_git_author_name="Bot", qswarm_git_author_email="not-an-email")
    with pytest.raises(ValueError, match="pr_git_author_not_configured"):
        git_author_from_settings(s)


def test_ensure_git_author_identity_sets_repo_local_config(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    s = Settings(
        qswarm_git_author_name="QSwarm CI",
        qswarm_git_author_email="ci@example.org",
    )
    ensure_git_author_identity(repo, settings=s)
    n = subprocess.run(
        ["git", "config", "--get", "user.name"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    e = subprocess.run(
        ["git", "config", "--get", "user.email"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert n.stdout.strip() == "QSwarm CI"
    assert e.stdout.strip() == "ci@example.org"
    loc = subprocess.run(
        ["git", "config", "--local", "--get", "user.email"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert loc.returncode == 0
    assert loc.stdout.strip() == "ci@example.org"
