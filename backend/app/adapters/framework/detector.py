"""Select the best framework adapter for a repo path."""

from __future__ import annotations

from pathlib import Path

from app.adapters.framework.base import FrameworkAdapter
from app.adapters.framework.playwright_adapter import PlaywrightAdapter
from app.adapters.framework.unknown_adapter import UnknownFrameworkAdapter

_playwright = PlaywrightAdapter()
_unknown = UnknownFrameworkAdapter()


def get_adapter_for_repo(repo_path: Path) -> FrameworkAdapter:
    """Return Playwright adapter if detected, otherwise the unknown fallback."""
    if _playwright.detect(repo_path):
        return _playwright
    return _unknown
