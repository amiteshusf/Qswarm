"""Canonical coding engine identifiers (Sprint 2 Milestone 1)."""

from __future__ import annotations

from enum import StrEnum


class CodingEngineName(StrEnum):
    """Supported ``automation_sessions.coding_engine`` values."""

    STUB = "stub"
    CLAUDE_CODE = "claude_code"
    COPILOT_AGENT = "copilot_agent"

    @classmethod
    def parse(cls, raw: str | None) -> CodingEngineName:
        key = (raw or "").strip().lower()
        for member in cls:
            if member.value == key:
                return member
        raise ValueError(f"unsupported_coding_engine:{key or 'empty'}")

    @classmethod
    def values(cls) -> frozenset[str]:
        return frozenset(m.value for m in cls)
