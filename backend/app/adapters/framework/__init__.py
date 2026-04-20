"""Framework detection and scanning adapters."""

from app.adapters.framework.detector import get_adapter_for_repo

__all__ = ["get_adapter_for_repo"]
