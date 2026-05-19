"""Playwright execution orchestration for automation jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.models.automation_job import AutomationJob
from app.services.framework_runtime_errors import ExecutionPlanError
from app.services.framework_runtime_models import ExecutionPlan
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path
from app.services.playwright_runner import build_playwright_command, run_playwright_test

MAX_STDOUT_TAIL = 8000
MAX_STDERR_TAIL = 8000
MAX_NOTES = 20


def resolve_target_test_file(job: AutomationJob) -> str | None:
    """
    Target spec path: ``change_plan_json.target_test_file``, else
    ``generated_patch_json.target_test_file``.
    """
    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else None
    if plan:
        t = plan.get("target_test_file")
        if isinstance(t, str) and t.strip():
            return t.strip().replace("\\", "/")
    gen = job.generated_patch_json if isinstance(job.generated_patch_json, dict) else None
    if gen:
        t = gen.get("target_test_file")
        if isinstance(t, str) and t.strip():
            return t.strip().replace("\\", "/")
    return None


def _tail(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[-max_len:]


def _safe_target_path(repo: Path, rel: str) -> Path:
    r = rel.strip().replace("\\", "/")
    if not r or ".." in r or r.startswith("/"):
        raise ValueError("invalid_target_path")
    dest = (repo / r).resolve()
    root = repo.resolve()
    dest.relative_to(root)
    return dest


def execution_prerequisites_met(job: AutomationJob) -> bool:
    if job.repo_path is None or not str(job.repo_path).strip():
        return False
    fw = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else None
    if not fw or fw.get("framework_type") != "playwright":
        return False
    if resolve_target_test_file(job) is None:
        return False
    if not isinstance(job.change_plan_json, dict) and not isinstance(job.generated_patch_json, dict):
        return False
    return True


def build_preflight_failure_result(
    *,
    target_test_file: str,
    command: list[str],
    note: str,
    framework_type: str = "playwright",
) -> dict[str, Any]:
    return {
        "framework_type": framework_type,
        "command": command,
        "target_test_file": target_test_file,
        "success": False,
        "exit_code": None,
        "duration_ms": 0,
        "stdout_tail": "",
        "stderr_tail": "",
        "artifact_paths": [],
        "notes": [note][:MAX_NOTES],
    }


def normalize_run_result(
    raw: dict[str, Any],
    *,
    target_test_file: str,
    framework_type: str = "playwright",
) -> dict[str, Any]:
    """Turn runner output into bounded persistence JSON."""
    notes: list[str] = []
    if raw.get("timed_out"):
        notes.append("Execution timed out")
    if raw.get("launch_error"):
        notes.append(str(raw["launch_error"]))

    exit_code = raw.get("exit_code")
    timed_out = bool(raw.get("timed_out"))
    launch_error = raw.get("launch_error")
    success = not timed_out and launch_error is None and exit_code == 0

    out: dict[str, Any] = {
        "framework_type": framework_type,
        "command": list(raw.get("command") or []),
        "target_test_file": target_test_file,
        "success": success,
        "exit_code": exit_code,
        "duration_ms": int(raw.get("duration_ms") or 0),
        "stdout_tail": _tail(str(raw.get("stdout") or ""), MAX_STDOUT_TAIL),
        "stderr_tail": _tail(str(raw.get("stderr") or ""), MAX_STDERR_TAIL),
        "artifact_paths": [],
        "notes": notes[:MAX_NOTES],
    }
    if launch_error:
        out["launch_error"] = str(launch_error)[:500]
    return out


def build_playwright_execution_plan(job: AutomationJob) -> ExecutionPlan:
    """
    Framework-runtime execution plan: resolved cwd + argv for Playwright.

    Raises:
        ExecutionPlanError: When prerequisites, repo path, or target cannot be resolved safely.
    """
    if not execution_prerequisites_met(job):
        raise ExecutionPlanError(
            "Execution prerequisites not met (framework summary, repo_path, plan/patch).",
            code="execution_prerequisites_missing",
        )

    fw = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else {}
    if fw.get("framework_type") != "playwright":
        raise ExecutionPlanError("Job is not configured for Playwright execution.", code="execution_not_playwright")

    target = resolve_target_test_file(job)
    if not target:
        raise ExecutionPlanError("Could not resolve target test file for execution.", code="execution_target_unresolvable")

    try:
        repo = resolve_repo_path(job.repo_path)
    except FrameworkScanError as e:
        raise ExecutionPlanError(
            getattr(e, "message", str(e)),
            code="execution_repo_path_invalid",
        ) from e

    cmd = build_playwright_command(target)
    return ExecutionPlan(
        command=cmd,
        cwd=str(repo.resolve()),
        target_scope=target,
        framework_name="playwright",
        notes="npx playwright test <target>",
    )


def run_playwright_execution_for_job(
    job: AutomationJob,
    *,
    timeout_sec: int | None = None,
    subprocess_run: Any | None = None,
) -> dict[str, Any]:
    """
    Resolve workspace and target, optionally run subprocess, return ``execution_result_json`` blob.

    Raises:
        ValueError: If callers skip ``execution_prerequisites_met`` (defensive).
    """
    if not execution_prerequisites_met(job):
        raise ValueError("execution_prerequisites_missing")

    try:
        plan = build_playwright_execution_plan(job)
    except ExecutionPlanError as e:
        tw = resolve_target_test_file(job) or "tests/unknown.spec.ts"
        return build_preflight_failure_result(
            target_test_file=tw,
            command=build_playwright_command(tw),
            note=e.message,
        )

    repo = Path(plan.cwd)
    target = plan.target_scope or ""

    try:
        dest = _safe_target_path(repo, target)
    except ValueError:
        return build_preflight_failure_result(
            target_test_file=target,
            command=plan.command,
            note="Target path is invalid or escapes the repository root",
        )

    if not dest.is_file():
        return build_preflight_failure_result(
            target_test_file=target,
            command=plan.command,
            note="Target test file is missing under repo_path",
        )

    settings = get_settings()
    timeout = timeout_sec if timeout_sec is not None else settings.playwright_execution_timeout_seconds
    raw = run_playwright_test(
        repo,
        target,
        timeout_sec=int(timeout),
        command=plan.command,
        subprocess_run=subprocess_run,
    )
    return normalize_run_result(raw, target_test_file=target, framework_type="playwright")
