"""Select coding provider from configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.coding.base import CodeIntelligenceProvider
from app.providers.coding.stub_provider import StubCodingProvider

if TYPE_CHECKING:
    from app.core.config import Settings


def get_coding_provider(settings: "Settings | None" = None) -> CodeIntelligenceProvider:
    """Return the configured provider; unknown names fall back to stub."""
    from app.core.config import get_settings

    s = settings or get_settings()
    name = (s.coding_provider or "stub").strip().lower()
    if name in ("stub", "mock", "local"):
        return StubCodingProvider()
    return StubCodingProvider()
