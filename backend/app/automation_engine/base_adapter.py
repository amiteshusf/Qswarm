"""Abstract coding-agent adapter (Sprint 2 control plane — Milestone 1 scaffold)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.automation_engine.engine_models import EngineCapability, EngineRequest, EngineResult
from app.automation_engine.types import CodeSessionContext


class CodingAgentAdapterBase(ABC):
    """
    Pluggable coding engine: QSwarm orchestrates; adapters perform engine-specific work.

    ``run_*`` methods receive a normalized :class:`EngineRequest` plus a :class:`CodeSessionContext`
    so Milestone 1 can delegate to existing job services without duplicating planning logic in
    the payload builder. External engines (Milestone 2) may rely primarily on ``EngineRequest``.
    """

    @property
    @abstractmethod
    def engine_name(self) -> str:
        ...

    @abstractmethod
    def get_capabilities(self) -> EngineCapability:
        ...

    def validate_config(self) -> bool:
        """Return True if orchestration may invoke ``run_*`` for this adapter."""
        return self.get_capabilities().configured

    @abstractmethod
    def run_initial_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        ...

    @abstractmethod
    def run_revision_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        ...

    @abstractmethod
    def run_manual_rerun_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        ...

    def run_plan_only_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        """Scan repo context and produce a change plan without generating code."""
        raise NotImplementedError(f"{self.engine_name} does not support plan-only requests")

    def run_execute_after_plan_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        """Generate code and execute after a plan has been approved."""
        raise NotImplementedError(f"{self.engine_name} does not support execute-after-plan requests")
