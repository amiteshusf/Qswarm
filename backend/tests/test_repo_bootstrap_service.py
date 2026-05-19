"""Tests for repo_bootstrap_service (Node npm ci / npm install)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.adapters.framework.playwright_adapter import PlaywrightAdapter
from app.core.config import Settings
from app.services.repo_bootstrap_service import (
    RepoBootstrapCommandMissingError,
    RepoBootstrapError,
    RepoBootstrapTimeoutError,
    bootstrap_node_workspace,
    bootstrap_result_to_audit_payload,
)


def _fake_npm_run_populates_hosted_layout(argv, *, cwd, timeout_seconds, env=None):
    """Mimic successful npm (hosted validation is handled in framework_runtime_service)."""
    if argv == ["npm", "--version"]:
        return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
    if len(argv) >= 2 and argv[0] == "npm" and argv[1] in ("ci", "install"):
        root = Path(cwd)
        (root / "node_modules").mkdir(parents=True, exist_ok=True)
        if PlaywrightAdapter().detect(root):
            pwt = root / "node_modules" / "@playwright" / "test"
            pwt.mkdir(parents=True, exist_ok=True)
            (pwt / "package.json").write_text("{}")
    return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}


def test_lockfile_selects_npm_ci(tmp_path: Path):
    import json

    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        calls.append(list(argv))
        return _fake_npm_run_populates_hosted_layout(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env)

    r = bootstrap_node_workspace(
        tmp_path,
        workspace_profile="hosted_materialized",
        settings=Settings(qswarm_bootstrap_timeout_seconds=60),
        subprocess_runner=fake_run,
    )
    assert r.bootstrap_required is True
    assert r.command == ["npm", "ci"]
    assert r.diagnostics is not None
    assert r.diagnostics.get("npm_cwd") == str(tmp_path.resolve())
    assert r.diagnostics.get("chosen_command") == ["npm", "ci"]
    assert calls[0] == ["npm", "--version"]
    assert calls[1] == ["npm", "ci"]


def test_lockfile_placeholder_selects_npm_install(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text("{}")
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        calls.append(list(argv))
        return _fake_npm_run_populates_hosted_layout(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env)

    r = bootstrap_node_workspace(
        tmp_path,
        workspace_profile="hosted_materialized",
        settings=Settings(qswarm_bootstrap_timeout_seconds=60),
        subprocess_runner=fake_run,
    )
    assert r.command == ["npm", "install"]
    assert r.detected_stack == "node_npm_lockfile_unusable"
    assert any(c[:2] == ["npm", "install"] for c in calls)


def test_package_json_only_selects_npm_install(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        calls.append(list(argv))
        return _fake_npm_run_populates_hosted_layout(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env)

    r = bootstrap_node_workspace(
        tmp_path,
        workspace_profile="hosted_materialized",
        settings=Settings(qswarm_bootstrap_timeout_seconds=60),
        subprocess_runner=fake_run,
    )
    assert r.command == ["npm", "install"]
    assert any(c == ["npm", "install"] for c in calls)


def test_no_manifest_skips(tmp_path: Path):
    (tmp_path / "README.md").write_text("x")

    def should_not_run(*a, **k):
        raise AssertionError("subprocess should not run")

    r = bootstrap_node_workspace(
        tmp_path,
        workspace_profile="hosted_materialized",
        settings=Settings(),
        subprocess_runner=should_not_run,
    )
    assert r.bootstrap_required is False
    assert r.detected_stack == "none"


def test_local_existing_skips_when_node_modules_nonempty(tmp_path: Path):
    import json

    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "x").write_text("1")

    def should_not_run(*a, **k):
        raise AssertionError("should not run")

    r = bootstrap_node_workspace(
        tmp_path,
        workspace_profile="local_existing",
        settings=Settings(qswarm_skip_bootstrap_if_node_modules=True),
        subprocess_runner=should_not_run,
    )
    assert r.bootstrap_required is False
    assert "skipped" in (r.notes or "").lower()


def test_hosted_materialized_runs_even_with_node_modules(tmp_path: Path):
    import json

    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "x").write_text("1")
    ran: list[list[str]] = []

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        ran.append(list(argv))
        return _fake_npm_run_populates_hosted_layout(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env)

    bootstrap_node_workspace(
        tmp_path,
        workspace_profile="hosted_materialized",
        settings=Settings(qswarm_bootstrap_timeout_seconds=60),
        subprocess_runner=fake_run,
    )
    assert any(x[:2] == ["npm", "ci"] for x in ran)


def test_npm_missing_raises(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")

    def bad_npm(argv, *, cwd, timeout_seconds, env=None):
        if argv == ["npm", "--version"]:
            return {"exit_code": 127, "stdout": "", "stderr": "not found", "duration_ms": 1, "timed_out": False}
        return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

    with pytest.raises(RepoBootstrapCommandMissingError) as ei:
        bootstrap_node_workspace(
            tmp_path,
            workspace_profile="hosted_materialized",
            settings=Settings(qswarm_bootstrap_timeout_seconds=60),
            subprocess_runner=bad_npm,
        )
    assert ei.value.code == "repo_bootstrap_npm_missing"


def test_npm_ci_failure_raises(tmp_path: Path):
    import json

    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        if argv == ["npm", "--version"]:
            return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
        return {"exit_code": 1, "stdout": "", "stderr": "ERESOLVE something", "duration_ms": 1, "timed_out": False}

    with pytest.raises(RepoBootstrapError) as ei:
        bootstrap_node_workspace(
            tmp_path,
            workspace_profile="hosted_materialized",
            settings=Settings(qswarm_bootstrap_timeout_seconds=60),
            subprocess_runner=fake_run,
        )
    assert ei.value.code == "repo_bootstrap_failed"
    assert "ERESOLVE" in ei.value.message


def test_timeout_raises(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        if argv == ["npm", "--version"]:
            return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
        return {"exit_code": None, "stdout": "", "stderr": "slow", "duration_ms": 999, "timed_out": True}

    with pytest.raises(RepoBootstrapTimeoutError) as ei:
        bootstrap_node_workspace(
            tmp_path,
            workspace_profile="hosted_materialized",
            settings=Settings(qswarm_bootstrap_timeout_seconds=60),
            subprocess_runner=fake_run,
        )
    assert ei.value.code == "repo_bootstrap_timeout"


def test_hosted_materialized_passes_production_safe_install_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NODE_ENV", "production")
    (tmp_path / "package.json").write_text("{}")
    captured: list[dict[str, str] | None] = []

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        captured.append(env)
        return _fake_npm_run_populates_hosted_layout(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env)

    bootstrap_node_workspace(
        tmp_path,
        workspace_profile="hosted_materialized",
        settings=Settings(qswarm_bootstrap_timeout_seconds=60),
        subprocess_runner=fake_run,
    )
    assert captured[0] is not None
    assert captured[0]["NPM_CONFIG_PRODUCTION"] == "false"
    assert captured[0]["NODE_ENV"] == "development"
    assert captured[1]["NPM_CONFIG_PRODUCTION"] == "false"
    assert captured[1]["NODE_ENV"] == "development"


def test_local_existing_does_not_force_hosted_install_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NODE_ENV", "production")
    (tmp_path / "package.json").write_text("{}")
    envs: list[dict[str, str] | None] = []

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        envs.append(env)
        if argv == ["npm", "--version"]:
            return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
        return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

    r = bootstrap_node_workspace(
        tmp_path,
        workspace_profile="local_existing",
        settings=Settings(qswarm_bootstrap_timeout_seconds=60, qswarm_skip_bootstrap_if_node_modules=False),
        subprocess_runner=fake_run,
    )
    assert r.bootstrap_required is True
    assert all(e is None for e in envs)


def test_audit_payload_shape():
    r = MagicMock()
    r.detected_stack = "node_npm_lockfile"
    r.bootstrap_required = True
    r.command = ["npm", "ci"]
    r.success = True
    r.exit_code = 0
    r.duration_ms = 12
    r.stdout_tail = "ok"
    r.stderr_tail = "warn"
    r.notes = None
    r.diagnostics = None
    p = bootstrap_result_to_audit_payload(r)
    assert p["command"] == ["npm", "ci"]
    assert "token" not in str(p).lower()
