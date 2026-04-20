"""Playwright execution service and result normalization."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.constants import AutomationJobStatus
from app.db.models.automation_job import AutomationJob
from app.services.execution_service import (
    execution_prerequisites_met,
    normalize_run_result,
    resolve_target_test_file,
    run_playwright_execution_for_job,
)


def _job(**kwargs) -> AutomationJob:
    defaults = dict(
        id=uuid.uuid4(),
        approved_case_id="E-1",
        workflow_run_id=None,
        repo_id=None,
        repo_path="/tmp/repo",
        base_branch="main",
        branch_name=None,
        requested_by="t",
        status=AutomationJobStatus.EXECUTING.value,
        blocked_reason=None,
        latest_attempt_number=0,
        framework_summary_json={"framework_type": "playwright"},
        case_input_json=None,
        case_spec_json={"title": "T"},
        repo_context_json={},
        change_plan_json={
            "target_test_file": "tests/x.spec.ts",
            "files_to_modify": ["tests/x.spec.ts"],
        },
        generated_patch_json={"target_test_file": "tests/from-gen.spec.ts"},
        execution_result_json=None,
        final_result_json=None,
    )
    defaults.update(kwargs)
    return AutomationJob(**defaults)


def test_resolve_target_prefers_change_plan():
    job = _job()
    assert resolve_target_test_file(job) == "tests/x.spec.ts"


def test_resolve_target_falls_back_to_generated_patch():
    job = _job(change_plan_json=None, generated_patch_json={"target_test_file": "tests/fallback.spec.ts"})
    assert resolve_target_test_file(job) == "tests/fallback.spec.ts"


def test_normalize_success():
    r = normalize_run_result(
        {
            "command": ["npx", "playwright", "test", "t.spec.ts"],
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "timed_out": False,
            "duration_ms": 50,
            "launch_error": None,
        },
        target_test_file="t.spec.ts",
    )
    assert r["success"] is True
    assert r["exit_code"] == 0
    assert "stdout_tail" in r


def test_normalize_timeout():
    r = normalize_run_result(
        {
            "command": ["npx", "playwright", "test", "t.spec.ts"],
            "exit_code": None,
            "stdout": "partial",
            "stderr": "err",
            "timed_out": True,
            "duration_ms": 120_000,
            "launch_error": None,
        },
        target_test_file="t.spec.ts",
    )
    assert r["success"] is False
    assert "timed out" in " ".join(r["notes"]).lower()


def test_normalize_launch_error_has_key():
    r = normalize_run_result(
        {
            "command": ["npx", "playwright", "test", "t.spec.ts"],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "duration_ms": 1,
            "launch_error": "npx not found",
        },
        target_test_file="t.spec.ts",
    )
    assert r["success"] is False
    assert r.get("launch_error")


def test_run_execution_monkeypatched_subprocess(tmp_path: Path, monkeypatch):
    spec = tmp_path / "tests" / "a.spec.ts"
    spec.parent.mkdir(parents=True)
    spec.write_text("// t\n", encoding="utf-8")

    def fake_run(cmd, cwd, capture_output, text, timeout, shell):
        assert cmd[:3] == ["npx", "playwright", "test"]
        assert cwd == str(tmp_path.resolve())
        assert shell is False
        return subprocess.CompletedProcess(cmd, 0, stdout="pass\n", stderr="")

    job = _job(
        repo_path=str(tmp_path.resolve()),
        change_plan_json={"target_test_file": "tests/a.spec.ts", "files_to_modify": ["tests/a.spec.ts"]},
        generated_patch_json=None,
    )
    assert execution_prerequisites_met(job)
    out = run_playwright_execution_for_job(job, subprocess_run=fake_run)
    assert out["success"] is True
    assert out["exit_code"] == 0


def test_run_execution_timeout(monkeypatch, tmp_path: Path):
    spec = tmp_path / "tests" / "a.spec.ts"
    spec.parent.mkdir(parents=True)
    spec.write_text("// t\n", encoding="utf-8")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout", 1))

    job = _job(
        repo_path=str(tmp_path.resolve()),
        change_plan_json={"target_test_file": "tests/a.spec.ts", "files_to_modify": ["tests/a.spec.ts"]},
        generated_patch_json=None,
    )
    out = run_playwright_execution_for_job(job, timeout_sec=1, subprocess_run=boom)
    assert out["success"] is False
    assert "timed out" in " ".join(out["notes"]).lower()


def test_preflight_missing_file(tmp_path: Path):
    (tmp_path / "tests").mkdir(parents=True)
    job = _job(
        repo_path=str(tmp_path.resolve()),
        change_plan_json={
            "target_test_file": "tests/ghost.spec.ts",
            "files_to_modify": ["tests/ghost.spec.ts"],
        },
        generated_patch_json=None,
    )
    out = run_playwright_execution_for_job(job, subprocess_run=None)
    assert out["success"] is False
    assert "missing" in " ".join(out["notes"]).lower()
