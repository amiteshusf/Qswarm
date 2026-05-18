"""Typed errors for coding-agent adapters (orchestration control plane)."""


class EngineAdapterError(Exception):
    """Base class for adapter failures surfaced to orchestration."""

    code: str = "engine_adapter_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class EngineConfigurationError(EngineAdapterError):
    """Engine missing configuration, disabled, or not implemented for execution (Milestone 1 placeholders)."""

    code = "engine_configuration"


class EngineAuthError(EngineAdapterError):
    """Authentication / authorization with the external engine failed."""

    code = "engine_auth"


class EngineRepoAccessError(EngineAdapterError):
    """Repository or workspace could not be accessed by the engine."""

    code = "engine_repo_access"


class EngineTimeoutError(EngineAdapterError):
    """Engine operation exceeded its timeout."""

    code = "engine_timeout"


class EngineMalformedOutputError(EngineAdapterError):
    """Engine returned output that could not be parsed or validated."""

    code = "engine_malformed_output"


class UnsupportedEngineError(EngineAdapterError):
    """No adapter registered for the requested engine name."""

    code = "unsupported_engine"
