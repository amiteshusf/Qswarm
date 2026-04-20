"""Validate local repo paths and run framework detection + scan."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.framework.detector import get_adapter_for_repo


class FrameworkScanError(Exception):
    """Raised when ``repo_path`` cannot be used for scanning."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def resolve_repo_path(repo_path: str | None) -> Path:
    """
    Normalize and validate ``repo_path`` for filesystem scanning.

    Raises:
        FrameworkScanError: missing path, not found, or not a directory.
    """
    if repo_path is None or not str(repo_path).strip():
        raise FrameworkScanError(
            "repo_path_required",
            "repo_path is required to start an automation job (local directory to scan)",
        )
    raw = str(repo_path).strip()
    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError as e:
        raise FrameworkScanError(
            "repo_path_not_found", f"Cannot resolve repo_path: {e}"
        ) from e
    if not path.exists():
        raise FrameworkScanError("repo_path_not_found", f"repo_path does not exist: {raw}")
    if not path.is_dir():
        raise FrameworkScanError(
            "repo_path_not_a_directory", f"repo_path is not a directory: {raw}"
        )
    return path


def scan_local_repo(repo_path: Path) -> dict[str, Any]:
    """
    Detect framework adapter and return a normalized summary dict.

    Raises:
        FrameworkScanError: on unexpected I/O errors during scan.
    """
    adapter = get_adapter_for_repo(repo_path)
    try:
        return adapter.scan(repo_path)
    except OSError as e:
        raise FrameworkScanError(
            "framework_scan_io_error", f"Failed to read repository: {e}"
        ) from e
