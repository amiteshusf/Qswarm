"""Coding / code-intelligence provider plugins."""

from app.providers.coding.base import CodeIntelligenceProvider
from app.providers.coding.registry import get_coding_provider

__all__ = ["CodeIntelligenceProvider", "get_coding_provider"]
