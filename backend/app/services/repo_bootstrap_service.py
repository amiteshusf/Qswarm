"""Node dependency bootstrap for automation workspaces (npm ci / npm install)."""

from __future__ import annotations

import logging
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


def _tail(text: str, max_chars: int = 6000) -> str:
    t = text or ""
    if len(t) <= max_chars:
        return t
    return "…" + t[-max_chars:]


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

    Priority: package-lock.json -> npm ci; else package.json -> npm install; else skip.
    """
    lock = workspace / "package-lock.json"
    pkg = workspace / "package.json"
    if lock.is_file():
        return (["npm", "ci"], "node_npm_lockfile")
    if pkg.is_file():
        return (["npm", "install"], "node_npm_package_only")
    return (None, "none")


def planned_npm_bootstrap_command(workspace: Path) -> tuple[list[str] | None, str]:
    """Return the npm argv that :func:`bootstrap_node_workspace` would run, if any."""
    return _select_npm_command(workspace.resolve())


def _ensure_npm_available(
    workspace: Path,
    *,
    subprocess_runner: Callable[..., dict[str, Any]] | None,
) -> None:
    run = subprocess_runner or run_subprocess_argv
    r = run(["npm", "--version"], cwd=workspace, timeout_seconds=15.0)
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
    if cmd is None:
        logger.info(
            "repo_bootstrap_skipped",
            extra={
                "workspace": str(ws),
                "profile": workspace_profile,
                "reason": "no_npm_manifest",
                "detected_stack": stack,
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
        )

    if workspace_profile == "local_existing" and s.qswarm_skip_bootstrap_if_node_modules:
        if _node_modules_nonempty(ws):
            logger.info(
                "repo_bootstrap_skipped",
                extra={
                    "workspace": str(ws),
                    "profile": workspace_profile,
                    "reason": "local_existing_node_modules_nonempty",
                    "detected_stack": stack,
                    "qswarm_skip_bootstrap_if_node_modules": True,
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
            )

    # hosted_materialized: never skip npm based on node_modules — stale/partial installs must be refreshed.
    _ensure_npm_available(ws, subprocess_runner=subprocess_runner)

    run = subprocess_runner or run_subprocess_argv
    timeout = float(s.qswarm_bootstrap_timeout_seconds)
    t0 = time.perf_counter()
    out = run(cmd, cwd=ws, timeout_seconds=timeout)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    if out.get("timed_out"):
        raise RepoBootstrapTimeoutError(
            f"{' '.join(cmd)} timed out after {int(timeout)}s. stderr tail: "
            f"{_tail(str(out.get('stderr') or ''), 1500)}"
        )

    exit_code = out.get("exit_code")
    if exit_code is None or exit_code != 0:
        msg = (
            f"{' '.join(cmd)} failed with exit {exit_code}. "
            f"stderr: {_tail(str(out.get('stderr') or ''), 2000)} "
            f"stdout: {_tail(str(out.get('stdout') or ''), 1000)}"
        )
        raise RepoBootstrapError(msg.strip(), code="repo_bootstrap_failed")

    logger.info(
        "repo_bootstrap_completed",
        extra={
            "workspace": str(ws),
            "profile": workspace_profile,
            "command": cmd,
            "duration_ms": duration_ms,
            "detected_stack": stack,
        },
    )

    return RepoBootstrapResult(
        detected_stack=stack,
        bootstrap_required=True,
        command=list(cmd),
        success=True,
        exit_code=int(exit_code) if exit_code is not None else 0,
        duration_ms=duration_ms,
        stdout_tail=_tail(str(out.get("stdout") or "")),
        stderr_tail=_tail(str(out.get("stderr") or "")),
        notes="npm bootstrap completed.",
    )


def bootstrap_result_to_audit_payload(result: RepoBootstrapResult) -> dict[str, Any]:
    return {
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
