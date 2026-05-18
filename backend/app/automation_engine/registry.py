"""Resolve coding engine id to adapter (Milestone 1 registry)."""

from __future__ import annotations

from app.automation_engine.base_adapter import CodingAgentAdapterBase
from app.automation_engine.coding_engine_names import CodingEngineName
from app.automation_engine.engine_errors import UnsupportedEngineError
from app.automation_engine.engine_models import EngineCapability
from app.automation_engine.claude_code_adapter import ClaudeCodeAdapter
from app.automation_engine.copilot_agent_adapter import CopilotAgentAdapter
from app.automation_engine.stub_adapter import StubCodingAgentAdapter
from app.core.config import Settings, get_settings


def resolve_coding_agent_adapter(
    engine_id: str,
    *,
    settings: Settings | None = None,
) -> CodingAgentAdapterBase:
    """Return the adapter for a known engine; raises :class:`UnsupportedEngineError` if unknown."""
    try:
        name = CodingEngineName.parse(engine_id)
    except ValueError as e:
        raise UnsupportedEngineError(str(e), code="unsupported_engine") from e

    s = settings or get_settings()
    if name == CodingEngineName.STUB:
        return StubCodingAgentAdapter()
    if name == CodingEngineName.CLAUDE_CODE:
        return ClaudeCodeAdapter(s)
    if name == CodingEngineName.COPILOT_AGENT:
        return CopilotAgentAdapter(s)
    raise UnsupportedEngineError(f"unsupported_engine:{name.value}", code="unsupported_engine")


def get_code_agent_adapter(engine_id: str, *, settings: Settings | None = None) -> CodingAgentAdapterBase:
    """Backward-compatible alias for :func:`resolve_coding_agent_adapter`."""
    return resolve_coding_agent_adapter(engine_id, settings=settings)


def supported_coding_engines() -> frozenset[str]:
    return CodingEngineName.values()


def list_known_engines() -> list[str]:
    return sorted(supported_coding_engines())


def list_adapter_capabilities(*, settings: Settings | None = None) -> list[EngineCapability]:
    """Capabilities for every registered engine (stub + placeholders)."""
    s = settings or get_settings()
    caps: list[EngineCapability] = []
    for eng in sorted(CodingEngineName.values()):
        caps.append(resolve_coding_agent_adapter(eng, settings=s).get_capabilities())
    return caps
