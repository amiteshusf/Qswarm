"""Framework/runtime dataclasses (no service imports — avoids circular import with execution_service)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FrameworkRuntimeProfile:
    """Normalized outcome of filesystem-based framework/runtime detection."""

    framework_family: str
    framework_name: str
    language: str
    package_manager: str | None
    build_tool: str | None
    bootstrap_strategy: str
    runtime_validation_strategy: str
    execution_strategy: str
    confidence: str
    notes: tuple[str, ...] = ()

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "framework_family": self.framework_family,
            "framework_name": self.framework_name,
            "language": self.language,
            "package_manager": self.package_manager,
            "build_tool": self.build_tool,
            "bootstrap_strategy": self.bootstrap_strategy,
            "runtime_validation_strategy": self.runtime_validation_strategy,
            "execution_strategy": self.execution_strategy,
            "confidence": self.confidence,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class RepoBootstrapPlan:
    """Planned dependency bootstrap for a workspace (command may be None if not applicable)."""

    command: list[str] | None
    required: bool
    validation_paths: tuple[str, ...]
    notes: str
    strategy_key: str


@dataclass(frozen=True)
class PlaywrightBrowserPreparationResult:
    """Outcome of hosted `npx playwright install` (e.g. chromium) before test execution."""

    command: tuple[str, ...]
    cwd: str
    success: bool
    exit_code: int | None
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    notes: str | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "success": self.success,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RuntimeValidationResult:
    success: bool
    checks_run: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    notes: str

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "checks_run": list(self.checks_run),
            "missing_requirements": list(self.missing_requirements),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class ExecutionPlan:
    """Resolved test execution invocation (used at execution time)."""

    command: list[str]
    cwd: str
    target_scope: str | None
    framework_name: str
    notes: str | None = None
