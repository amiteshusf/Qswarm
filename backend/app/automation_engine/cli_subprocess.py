"""Run external coding CLIs without shell — explicit argv + cwd + timeout."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any


def run_subprocess_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Run ``argv`` with ``cwd`` as working directory.

    Returns a dict with ``exit_code``, ``stdout``, ``stderr``, ``duration_ms``, ``timed_out``.
    """
    if not argv:
        raise ValueError("argv_empty")
    merged = {**os.environ, **(env or {})}
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=merged,
            check=False,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "duration_ms": duration_ms,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {
            "exit_code": None,
            "stdout": out,
            "stderr": err,
            "duration_ms": duration_ms,
            "timed_out": True,
        }
