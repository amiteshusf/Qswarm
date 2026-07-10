"""Temporary diagnostics for Copilot CLI command resolution from the running app process.

Remove after Render PATH / binary visibility is understood.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings


def build_copilot_command_diagnostics(
    settings: Settings | None = None,
    *,
    help_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Inspect ``QSWARM_COPILOT_AGENT_COMMAND`` the same way the adapter would, with extra detail."""
    s = settings or get_settings()
    raw = (s.qswarm_copilot_agent_command or "").strip()

    out: dict[str, Any] = {
        "temporary": True,
        "purpose": "Diagnose Copilot CLI resolution from the backend process (not the Render shell).",
        "qswarm_copilot_agent_enabled": s.qswarm_copilot_agent_enabled,
        "raw_command": raw,
        "process": {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "user": os.environ.get("USER") or os.environ.get("LOGNAME"),
        },
        "path_env": os.environ.get("PATH", ""),
        "checks": {},
        "adapter_resolution": {},
        "help_probe": None,
        "errors": [],
    }

    if not raw:
        out["errors"].append({"step": "raw_command", "message": "QSWARM_COPILOT_AGENT_COMMAND is empty"})
        return out

    checks: dict[str, Any] = out["checks"]

    def _record_error(step: str, exc: BaseException) -> None:
        out["errors"].append({"step": step, "type": type(exc).__name__, "message": str(exc)})

    try:
        checks["os_path_exists"] = os.path.exists(raw)
    except Exception as e:
        checks["os_path_exists"] = None
        _record_error("os_path_exists", e)

    try:
        checks["os_path_isfile"] = os.path.isfile(raw)
    except Exception as e:
        checks["os_path_isfile"] = None
        _record_error("os_path_isfile", e)

    try:
        checks["os_access_x_ok"] = os.access(raw, os.X_OK)
    except Exception as e:
        checks["os_access_x_ok"] = None
        _record_error("os_access_x_ok", e)

    try:
        checks["pathlib_is_file"] = Path(raw).is_file()
    except Exception as e:
        checks["pathlib_is_file"] = None
        _record_error("pathlib_is_file", e)

    try:
        checks["shutil_which"] = shutil.which(raw)
    except Exception as e:
        checks["shutil_which"] = None
        _record_error("shutil_which", e)

    try:
        checks["pathlib_resolve"] = str(Path(raw).resolve())
    except Exception as e:
        checks["pathlib_resolve"] = None
        _record_error("pathlib_resolve", e)

    # Mirror CopilotAgentAdapter._resolve_cli_executable without raising.
    adapter: dict[str, Any] = out["adapter_resolution"]
    p = Path(raw)
    try:
        adapter["path_is_file"] = p.is_file()
    except Exception as e:
        adapter["path_is_file"] = None
        _record_error("adapter_path_is_file", e)

    if adapter.get("path_is_file"):
        try:
            adapter["resolved_executable"] = str(p.resolve())
            adapter["would_succeed"] = True
        except Exception as e:
            adapter["resolved_executable"] = None
            adapter["would_succeed"] = False
            _record_error("adapter_path_resolve", e)
    else:
        try:
            found = shutil.which(raw)
        except Exception as e:
            found = None
            _record_error("adapter_shutil_which", e)
        adapter["shutil_which"] = found
        if found:
            adapter["resolved_executable"] = found
            adapter["would_succeed"] = True
        else:
            adapter["resolved_executable"] = None
            adapter["would_succeed"] = False
            adapter["failure_message"] = (
                f"Copilot CLI not found (not a file and not on PATH): {raw!r}"
            )

    help_target = adapter.get("resolved_executable") or raw
    probe: dict[str, Any] = {
        "argv": [help_target, "--help"],
        "timeout_seconds": help_timeout_seconds,
    }
    try:
        proc = subprocess.run(
            [help_target, "--help"],
            capture_output=True,
            text=True,
            timeout=help_timeout_seconds,
            env=os.environ,
            cwd=os.getcwd(),
            check=False,
        )
        probe["exit_code"] = proc.returncode
        probe["stdout_tail"] = (proc.stdout or "")[-4000:]
        probe["stderr_tail"] = (proc.stderr or "")[-4000:]
        probe["timed_out"] = False
    except subprocess.TimeoutExpired as e:
        probe["exit_code"] = None
        probe["stdout_tail"] = (
            e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        )[-4000:]
        probe["stderr_tail"] = (
            e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        )[-4000:]
        probe["timed_out"] = True
        _record_error("help_probe", e)
    except Exception as e:
        probe["exit_code"] = None
        probe["timed_out"] = False
        probe["spawn_error"] = str(e)
        _record_error("help_probe", e)

    out["help_probe"] = probe
    return out
