"""Coding-agent adapter scaffold (Sprint 2 Milestone 1)."""

from app.automation_engine.base_adapter import CodingAgentAdapterBase
from app.automation_engine.coding_engine_names import CodingEngineName
from app.automation_engine.engine_errors import (
    EngineAdapterError,
    EngineAuthError,
    EngineConfigurationError,
    EngineMalformedOutputError,
    EngineRepoAccessError,
    EngineTimeoutError,
    UnsupportedEngineError,
)
from app.automation_engine.engine_models import (
    EngineCapability,
    EngineRequest,
    EngineResult,
    EngineResultStatus,
    EngineTaskType,
)
from app.automation_engine.registry import (
    get_code_agent_adapter,
    list_adapter_capabilities,
    list_known_engines,
    resolve_coding_agent_adapter,
    supported_coding_engines,
)
from app.automation_engine.types import CodeSessionContext, PatchResult, PlanResult

__all__ = [
    "CodingAgentAdapterBase",
    "CodingEngineName",
    "CodeSessionContext",
    "EngineAdapterError",
    "EngineAuthError",
    "EngineCapability",
    "EngineConfigurationError",
    "EngineMalformedOutputError",
    "EngineRepoAccessError",
    "EngineRequest",
    "EngineResult",
    "EngineResultStatus",
    "EngineTaskType",
    "EngineTimeoutError",
    "PatchResult",
    "PlanResult",
    "UnsupportedEngineError",
    "get_code_agent_adapter",
    "list_adapter_capabilities",
    "list_known_engines",
    "resolve_coding_agent_adapter",
    "supported_coding_engines",
]
