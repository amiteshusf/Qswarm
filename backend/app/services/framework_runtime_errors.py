"""Typed errors for framework detection, hosted preparation, runtime validation, and execution plans."""

from __future__ import annotations


class FrameworkRuntimeError(Exception):
    """Base for framework-runtime pipeline errors (message + stable code for APIs)."""

    def __init__(self, message: str, *, code: str):
        self.message = message
        self.code = code
        super().__init__(message)


class HostedExecutionPreparationError(FrameworkRuntimeError):
    """Hosted materialized workspace failed detection, policy, or post-bootstrap validation."""


class FrameworkDetectionError(HostedExecutionPreparationError):
    """Workspace layout could not be interpreted for framework detection."""

    def __init__(self, message: str, *, code: str = "framework_detection_failed"):
        super().__init__(message, code=code)


class UnsupportedHostedFrameworkError(HostedExecutionPreparationError):
    """Detected framework is not yet supported for automated hosted bootstrap/execution."""

    def __init__(self, message: str, *, code: str = "hosted_framework_not_supported"):
        super().__init__(message, code=code)


class RuntimeValidationError(HostedExecutionPreparationError):
    """Post-bootstrap workspace does not satisfy runtime readiness for the detected framework."""

    def __init__(self, message: str, *, code: str = "runtime_validation_failed"):
        super().__init__(message, code=code)


class ExecutionPlanError(FrameworkRuntimeError):
    """Could not build a safe execution command for the job (e.g. missing target)."""

    def __init__(self, message: str, *, code: str = "execution_plan_failed"):
        super().__init__(message, code=code)
