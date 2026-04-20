"""Subprocess Playwright test runner (explicit argv, no shell)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable


def build_playwright_command(target_test_file: str) -> list[str]:
    """Fixed argv for `npx playwright test <file>`."""
    rel = target_test_file.strip().replace("\\", "/")
    return ["npx", "playwright", "test", rel]


def run_playwright_test(
    repo_root: Path,
    target_test_file: str,
    *,
    timeout_sec: int,
    subprocess_run: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """
    Run Playwright in ``repo_root`` with ``cwd`` set to the repo.

    Returns a dict suitable for normalization (stdout/stderr may be large).
    """
    cmd = build_playwright_command(target_test_file)
    runner = subprocess_run or subprocess.run
    start = time.monotonic()

    try:
        proc = runner(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            shell=False,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "command": cmd,
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "timed_out": False,
            "duration_ms": duration_ms,
            "launch_error": None,
        }
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        raw_out = getattr(e, "output", None)
        raw_err = getattr(e, "stderr", None)
        if isinstance(raw_out, bytes):
            out = raw_out.decode("utf-8", errors="replace")
        else:
            out = (raw_out or "") if isinstance(raw_out, str) else ""
        if isinstance(raw_err, bytes):
            err = raw_err.decode("utf-8", errors="replace")
        else:
            err = (raw_err or "") if isinstance(raw_err, str) else ""
        return {
            "command": cmd,
            "exit_code": None,
            "stdout": out,
            "stderr": err or str(e),
            "timed_out": True,
            "duration_ms": duration_ms,
            "launch_error": None,
        }
    except FileNotFoundError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "duration_ms": duration_ms,
            "launch_error": f"Executable not found: {e}",
        }
    except OSError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "duration_ms": duration_ms,
            "launch_error": str(e),
        }
