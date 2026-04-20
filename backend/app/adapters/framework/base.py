"""Abstract framework adapter — one implementation per test stack (Playwright, Cypress, …)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class FrameworkAdapter(ABC):
    """Detect and scan a local repo for test-framework structure."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. ``playwright``."""

    @abstractmethod
    def detect(self, repo_path: Path) -> bool:
        """Return True if this adapter should handle ``repo_path``."""

    @abstractmethod
    def scan(self, repo_path: Path) -> dict[str, Any]:
        """
        Produce a JSON-serializable summary dict.

        Keys should align across adapters for downstream consumers.
        """
