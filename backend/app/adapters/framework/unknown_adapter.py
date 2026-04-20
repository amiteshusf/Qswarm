"""Fallback when no concrete framework matches."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.framework.base import FrameworkAdapter


def unknown_summary() -> dict[str, Any]:
    return {
        "framework_type": "unknown",
        "language": None,
        "package_manager": None,
        "config_files": [],
        "test_root": None,
        "runner_command": None,
        "test_file_patterns": [],
        "page_object_dirs": [],
        "fixture_files": [],
        "helper_dirs": [],
        "similar_test_files": [],
        "notes": ["No supported framework detected"],
        "missing_capabilities": ["Framework adapter not found"],
    }


class UnknownFrameworkAdapter(FrameworkAdapter):
    @property
    def name(self) -> str:
        return "unknown"

    def detect(self, repo_path: Path) -> bool:
        return True

    def scan(self, repo_path: Path) -> dict[str, Any]:
        return unknown_summary()
