"""Source-control provider adapters (PR / MR creation)."""

from app.source_control.registry import resolve_source_control_adapter

__all__ = ["resolve_source_control_adapter"]
