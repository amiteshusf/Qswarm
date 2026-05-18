"""Backward-compatible re-export — prefer :mod:`app.automation_engine.cli_subprocess`."""

from app.automation_engine.cli_subprocess import run_subprocess_argv

__all__ = ["run_subprocess_argv"]
