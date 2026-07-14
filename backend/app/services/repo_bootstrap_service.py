"""Node dependency bootstrap for automation workspaces (npm ci / npm install)."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from app.automation_engine.cli_subprocess import run_subprocess_argv
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

WorkspaceProfile = Literal["hosted_materialized", "local_existing"]


class RepoBootstrapError(Exception):
    """Dependency bootstrap failed (npm missing, install error, etc.)."""

    def __init__(self, message: str, *, code: str):
        self.message = message
        self.code = code
        super().__init__(message)


class RepoBootstrapTimeoutError(RepoBootstrapError):
    def __init__(self, message: str):
        super().__init__(message, code="repo_bootstrap_timeout")


class RepoBootstrapCommandMissingError(RepoBootstrapError):
    def __init__(self, message: str):
        super().__init__(message, code="repo_bootstrap_npm_missing")


@dataclass(frozen=True)
class RepoBootstrapResult:
    detected_stack: str
    bootstrap_required: bool
    command: list[str] | None
    success: bool
    exit_code: int | None
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    notes: str | None = None
    # Non-secret paths/flags for audit (stdout/stderr tails live on the result fields).
    diagnostics: dict[str, Any] | None = None


def _tail(text: str, max_chars: int = 6000) -> str:
    t = text or ""
    if len(t) <= max_chars:
        return t
    return "…" + t[-max_chars:]


def package_lock_usable_for_npm_ci(workspace: Path) -> bool:
    """
    ``npm ci`` requires a coherent lockfile. Empty ``{}`` or placeholder locks should fall back to ``npm install``.
    """
    p = workspace / "package-lock.json"
    if not p.is_file():
        return False
    try:
        if p.stat().st_size < 12:
            return False
    except OSError:
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict) or "lockfileVersion" not in data:
        return False
    try:
        lv = int(data.get("lockfileVersion", 0))
    except (TypeError, ValueError):
        return False
    if lv >= 2:
        pkgs = data.get("packages")
        return isinstance(pkgs, dict) and len(pkgs) > 0
    deps = data.get("dependencies")
    return isinstance(deps, dict) and len(deps) > 0


def _node_modules_nonempty(workspace: Path) -> bool:
    nm = workspace / "node_modules"
    try:
        if not nm.is_dir():
            return False
        return any(nm.iterdir())
    except OSError:
        return False


def _select_npm_command(workspace: Path) -> tuple[list[str] | None, str]:
    """
    Returns (argv for npm, detected_stack).

    Priority: usable package-lock.json -> npm ci; lock present but unusable -> npm install;
    else package.json -> npm install; else skip.
    """
    lock = workspace / "package-lock.json"
    pkg = workspace / "package.json"
    if lock.is_file():
        if package_lock_usable_for_npm_ci(workspace):
            return (["npm", "ci"], "node_npm_lockfile")
        return (["npm", "install"], "node_npm_lockfile_unusable")
    if pkg.is_file():
        return (["npm", "install"], "node_npm_package_only")
    return (None, "none")


def planned_npm_bootstrap_command(workspace: Path) -> tuple[list[str] | None, str]:
    """Return the npm argv that :func:`bootstrap_node_workspace` would run, if any."""
    return _select_npm_command(workspace.resolve())


def _hosted_materialized_bootstrap_env() -> dict[str, str]:
    """
    Hosted clones often run with NODE_ENV=production / production npm config, which omits devDependencies.
    Playwright and most test runners live in devDependencies — install them explicitly for this subprocess tree.
    """
    extra: dict[str, str] = {"NPM_CONFIG_PRODUCTION": "false"}
    if os.environ.get("NODE_ENV") == "production":
        extra["NODE_ENV"] = "development"
    return extra


def _ensure_npm_available(
    workspace: Path,
    *,
    subprocess_runner: Callable[..., dict[str, Any]] | None,
    env: dict[str, str] | None = None,
) -> None:
    run = subprocess_runner or run_subprocess_argv
    r = run(["npm", "--version"], cwd=workspace, timeout_seconds=15.0, env=env)
    if r.get("timed_out"):
        raise RepoBootstrapTimeoutError("npm --version timed out (npm may be missing or unreachable).")
    ec = r.get("exit_code")
    if ec is not None and ec != 0:
        err = _tail(str(r.get("stderr") or r.get("stdout") or ""), 800)
        raise RepoBootstrapCommandMissingError(
            f"npm is not available or failed self-check: {err}".strip() or "npm self-check failed."
        )


def bootstrap_node_workspace(
    workspace: Path,
    *,
    workspace_profile: WorkspaceProfile,
    settings: Settings | None = None,
    subprocess_runner: Callable[..., dict[str, Any]] | None = None,
) -> RepoBootstrapResult:
    """
    Install Node dependencies when a ``package.json`` / lockfile is present.

    **hosted_materialized:** always run npm ci/install when a manifest exists (fresh clone).

    **local_existing:** skip when ``node_modules`` is already populated; otherwise install.
    """
    s = settings or get_settings()
    ws = workspace.resolve()

    cmd, stack = _select_npm_command(ws)
    lock_path = ws / "package-lock.json"
    pkg_path = ws / "package.json"
    nm_path = ws / "node_modules"
    pwt_path = nm_path / "@playwright" / "test"
    lock_present = lock_path.is_file()
    lock_usable = package_lock_usable_for_npm_ci(ws) if lock_present else None

    if cmd is None:
        diag_skip: dict[str, Any] = {
            "resolved_workspace_path": str(ws),
            "npm_cwd": str(ws),
            "package_json_present": pkg_path.is_file(),
            "package_lock_present": lock_present,
            "package_lock_usable_for_npm_ci": lock_usable,
            "skip_reason": "no_npm_manifest",
        }
        logger.debug(
            "repo_bootstrap_skipped",
            extra={
                "workspace": str(ws),
                "profile": workspace_profile,
                "reason": "no_npm_manifest",
                "detected_stack": stack,
                **{k: v for k, v in diag_skip.items() if k != "skip_reason"},
            },
        )
        return RepoBootstrapResult(
            detected_stack=stack,
            bootstrap_required=False,
            command=None,
            success=True,
            exit_code=None,
            duration_ms=0,
            stdout_tail="",
            stderr_tail="",
            notes="No package.json / package-lock.json; bootstrap skipped.",
            diagnostics=diag_skip,
        )

    if workspace_profile == "local_existing" and s.qswarm_skip_bootstrap_if_node_modules:
        if _node_modules_nonempty(ws):
            diag_skip = {
                "resolved_workspace_path": str(ws),
                "npm_cwd": str(ws),
                "package_json_present": pkg_path.is_file(),
                "package_lock_present": lock_present,
                "package_lock_usable_for_npm_ci": lock_usable,
                "node_modules_exists_before": nm_path.is_dir(),
                "skip_reason": "local_existing_node_modules_nonempty",
            }
            logger.info(
                "repo_bootstrap_skipped",
                extra={
                    "workspace": str(ws),
                    "profile": workspace_profile,
                    "reason": "local_existing_node_modules_nonempty",
                    "detected_stack": stack,
                    "qswarm_skip_bootstrap_if_node_modules": True,
                    **{k: v for k, v in diag_skip.items() if k != "skip_reason"},
                },
            )
            return RepoBootstrapResult(
                detected_stack=stack,
                bootstrap_required=False,
                command=None,
                success=True,
                exit_code=None,
                duration_ms=0,
                stdout_tail="",
                stderr_tail="",
                notes="local_existing profile: node_modules present; bootstrap skipped.",
                diagnostics=diag_skip,
            )

    # hosted_materialized: never skip npm based on node_modules — stale/partial installs must be refreshed.
    hosted_npm_env: dict[str, str] | None = (
        _hosted_materialized_bootstrap_env() if workspace_profile == "hosted_materialized" else None
    )
    _ensure_npm_available(ws, subprocess_runner=subprocess_runner, env=hosted_npm_env)

    run = subprocess_runner or run_subprocess_argv
    timeout = float(s.qswarm_bootstrap_timeout_seconds)
    node_modules_before = nm_path.is_dir()
    diag_pre: dict[str, Any] = {
        "resolved_workspace_path": str(ws),
        "npm_cwd": str(ws),
        "chosen_command": list(cmd),
        "package_json_present": pkg_path.is_file(),
        "package_lock_present": lock_present,
        "package_lock_usable_for_npm_ci": lock_usable,
        "node_modules_exists_before": node_modules_before,
        "hosted_bootstrap_env": (
            {"NPM_CONFIG_PRODUCTION": "false", "NODE_ENV_overridden_for_install": os.environ.get("NODE_ENV") == "production"}
            if workspace_profile == "hosted_materialized"
            else False
        ),
    }
    t0 = time.perf_counter()
    out = run(cmd, cwd=ws, timeout_seconds=timeout, env=hosted_npm_env)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    if out.get("timed_out"):
        raise RepoBootstrapTimeoutError(
            f"{' '.join(cmd)} timed out after {int(timeout)}s (cwd={ws}). stderr tail: "
            f"{_tail(str(out.get('stderr') or ''), 1500)}"
        )

    exit_code = out.get("exit_code")
    if exit_code is None or exit_code != 0:
        msg = (
            f"{' '.join(cmd)} failed with exit {exit_code} (cwd={ws}). "
            f"stderr: {_tail(str(out.get('stderr') or ''), 2000)} "
            f"stdout: {_tail(str(out.get('stdout') or ''), 1000)}"
        )
        logger.warning(
            "repo_bootstrap_failed",
            extra={
                "workspace": str(ws),
                "bootstrap_cwd": str(ws),
                "command": cmd,
                "npm_exit_code": exit_code,
                **diag_pre,
            },
        )
        raise RepoBootstrapError(msg.strip(), code="repo_bootstrap_failed")

    ec_int = int(exit_code) if exit_code is not None else 0
    diag_done = {
        **diag_pre,
        "npm_exit_code": ec_int,
        "node_modules_exists_after": nm_path.is_dir(),
        "playwright_test_pkg_exists_after": pwt_path.is_dir(),
        "node_modules_path_checked": str(nm_path),
        "playwright_test_path_checked": str(pwt_path),
    }
    logger.debug(
        "repo_bootstrap_completed",
        extra={
            "workspace": str(ws),
            "bootstrap_cwd": str(ws),
            "profile": workspace_profile,
            "command": cmd,
            "npm_exit_code": ec_int,
            "duration_ms": duration_ms,
            "detected_stack": stack,
            "node_modules_exists_after": diag_done["node_modules_exists_after"],
            "playwright_test_pkg_exists_after": diag_done["playwright_test_pkg_exists_after"],
        },
    )

    return RepoBootstrapResult(
        detected_stack=stack,
        bootstrap_required=True,
        command=list(cmd),
        success=True,
        exit_code=ec_int,
        duration_ms=duration_ms,
        stdout_tail=_tail(str(out.get("stdout") or "")),
        stderr_tail=_tail(str(out.get("stderr") or "")),
        notes="npm bootstrap completed.",
        diagnostics=diag_done,
    )


def bootstrap_result_to_audit_payload(result: RepoBootstrapResult) -> dict[str, Any]:
    p: dict[str, Any] = {
        "detected_stack": result.detected_stack,
        "bootstrap_required": result.bootstrap_required,
        "command": result.command,
        "success": result.success,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "stdout_tail": result.stdout_tail,
        "stderr_tail": result.stderr_tail,
        "notes": result.notes,
    }
    if result.diagnostics is not None:
        p["bootstrap_diagnostics"] = result.diagnostics
    return p
