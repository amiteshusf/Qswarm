"""
Framework / runtime detection and hosted execution preparation.

Pipeline for hosted materialized workspaces:
  detect -> bootstrap plan -> npm (when applicable) -> runtime validation -> (execution plan at run time)

Playwright is the first fully supported hosted framework. Other stacks are detected and rejected
cleanly until incremental support is added.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.adapters.framework.playwright_adapter import PlaywrightAdapter
from app.core.config import Settings, get_settings
from app.services.framework_runtime_models import (
    FrameworkRuntimeProfile,
    RepoBootstrapPlan,
    RuntimeValidationResult,
)
from app.services.framework_runtime_errors import (
    FrameworkDetectionError,
    RuntimeValidationError,
    UnsupportedHostedFrameworkError,
)
from app.services.repo_bootstrap_service import (
    RepoBootstrapResult,
    bootstrap_node_workspace,
)

logger = logging.getLogger(__name__)

_playwright = PlaywrightAdapter()

# Frameworks with full hosted bootstrap + validation in this codebase (expand over time).
HOSTED_FULLY_SUPPORTED: frozenset[str] = frozenset({"playwright"})

# Detected-only (clear failure on hosted, no silent fall-through).
HOSTED_DETECTED_ONLY: frozenset[str] = frozenset(
    {"webdriverio", "cypress", "selenium_maven", "selenium_gradle", "pytest_python", "unknown"}
)


@dataclass(frozen=True)
class HostedExecutionPreparation:
    """Outcome of hosted materialized detect + bootstrap + runtime validation."""

    profile: FrameworkRuntimeProfile
    bootstrap_result: RepoBootstrapResult
    runtime_validation: RuntimeValidationResult


def _read_package_json(root: Path) -> dict[str, Any] | None:
    p = root / "package.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None


def _merged_npm_dep_keys(data: dict[str, Any]) -> frozenset[str]:
    keys: set[str] = set()
    for block_name in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(block_name)
        if isinstance(block, dict):
            keys.update(block.keys())
    return frozenset(keys)


def _has_python_pytest_signals(root: Path) -> bool:
    if (root / "pyproject.toml").is_file():
        return True
    if (root / "pytest.ini").is_file():
        return True
    if (root / "requirements.txt").is_file():
        return True
    if (root / "conftest.py").is_file():
        return True
    return False


def _cypress_signals(root: Path) -> bool:
    for p in root.glob("cypress.config.*"):
        if p.is_file():
            return True
    data = _read_package_json(root)
    if not data:
        return False
    return "cypress" in _merged_npm_dep_keys(data)


def _webdriverio_signals(root: Path) -> bool:
    for p in root.iterdir():
        if p.is_file() and p.name.startswith("wdio.conf"):
            return True
    data = _read_package_json(root)
    if not data:
        return False
    deps = _merged_npm_dep_keys(data)
    return "webdriverio" in deps or "@wdio/cli" in deps


def detect_framework_runtime(workspace: Path) -> FrameworkRuntimeProfile:
    """
    Conservative, explainable detection. Single primary framework per workspace root.

    Priority: JVM build files > Node package.json (E2E) > Python test signals > unknown.
    """
    try:
        root = workspace.resolve()
    except OSError as e:
        raise FrameworkDetectionError(
            f"Cannot resolve workspace path: {e}",
            code="framework_workspace_unresolvable",
        ) from e

    if not root.is_dir():
        raise FrameworkDetectionError(
            "Workspace path is not a directory.",
            code="framework_workspace_invalid",
        )

    notes: list[str] = []

    if (root / "pom.xml").is_file():
        notes.append("Detected pom.xml (Maven).")
        return FrameworkRuntimeProfile(
            framework_family="jvm",
            framework_name="selenium_maven",
            language="java",
            package_manager="maven",
            build_tool="maven",
            bootstrap_strategy="mvn_compile_skip_tests",
            runtime_validation_strategy="jvm_not_implemented",
            execution_strategy="maven_test_not_implemented",
            confidence="high",
            notes=tuple(notes),
        )

    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        notes.append("Detected Gradle build file.")
        return FrameworkRuntimeProfile(
            framework_family="jvm",
            framework_name="selenium_gradle",
            language="java",
            package_manager=None,
            build_tool="gradle",
            bootstrap_strategy="gradle_test_classes",
            runtime_validation_strategy="jvm_not_implemented",
            execution_strategy="gradle_test_not_implemented",
            confidence="high",
            notes=tuple(notes),
        )

    if (root / "package.json").is_file():
        if _playwright.detect(root):
            notes.append("Detected Playwright (config and/or @playwright/test in package.json).")
            return FrameworkRuntimeProfile(
                framework_family="e2e_web",
                framework_name="playwright",
                language="typescript_or_javascript",
                package_manager="npm",
                build_tool=None,
                bootstrap_strategy="npm_ci_or_install",
                runtime_validation_strategy="playwright_npm_layout",
                execution_strategy="playwright_npx",
                confidence="high",
                notes=tuple(notes),
            )
        if _webdriverio_signals(root):
            notes.append("Detected WebdriverIO (wdio.conf* and/or webdriverio / @wdio/cli in package.json).")
            return FrameworkRuntimeProfile(
                framework_family="e2e_web",
                framework_name="webdriverio",
                language="javascript",
                package_manager="npm",
                build_tool=None,
                bootstrap_strategy="npm_ci_or_install",
                runtime_validation_strategy="wdio_not_implemented",
                execution_strategy="wdio_cli_not_implemented",
                confidence="high",
                notes=tuple(notes),
            )
        if _cypress_signals(root):
            notes.append("Detected Cypress (cypress.config.* and/or cypress dependency).")
            return FrameworkRuntimeProfile(
                framework_family="e2e_web",
                framework_name="cypress",
                language="javascript",
                package_manager="npm",
                build_tool=None,
                bootstrap_strategy="npm_ci_or_install",
                runtime_validation_strategy="cypress_not_implemented",
                execution_strategy="cypress_cli_not_implemented",
                confidence="high",
                notes=tuple(notes),
            )
        notes.append("package.json present but no supported E2E framework markers matched.")
        return FrameworkRuntimeProfile(
            framework_family="node",
            framework_name="unknown",
            language="javascript",
            package_manager="npm",
            build_tool=None,
            bootstrap_strategy="npm_ci_or_install",
            runtime_validation_strategy="unknown",
            execution_strategy="unknown",
            confidence="low",
            notes=tuple(notes),
        )

    if _has_python_pytest_signals(root):
        notes.append("Detected Python/pytest signals (pyproject.toml, pytest.ini, requirements.txt, or conftest.py).")
        return FrameworkRuntimeProfile(
            framework_family="python",
            framework_name="pytest_python",
            language="python",
            package_manager="pip",
            build_tool=None,
            bootstrap_strategy="pip_install_requirements",
            runtime_validation_strategy="python_venv_not_implemented",
            execution_strategy="pytest_not_implemented",
            confidence="medium",
            notes=tuple(notes),
        )

    notes.append("No recognizable test framework markers at repository root.")
    return FrameworkRuntimeProfile(
        framework_family="unknown",
        framework_name="unknown",
        language="unknown",
        package_manager=None,
        build_tool=None,
        bootstrap_strategy="none",
        runtime_validation_strategy="none",
        execution_strategy="none",
        confidence="low",
        notes=tuple(notes),
    )


def assert_hosted_framework_supported(profile: FrameworkRuntimeProfile) -> None:
    """Fail fast on hosted when the stack is not yet wired for automated preparation."""
    if profile.framework_name in HOSTED_FULLY_SUPPORTED:
        return
    label = profile.framework_name
    if label in HOSTED_DETECTED_ONLY or label == "unknown":
        raise UnsupportedHostedFrameworkError(
            f"Hosted execution is not yet enabled for framework '{label}'. "
            f"Currently supported for hosted materialized workspaces: {', '.join(sorted(HOSTED_FULLY_SUPPORTED))}.",
            code="hosted_framework_not_supported",
        )
    raise UnsupportedHostedFrameworkError(
        f"Hosted execution is not supported for framework '{label}'.",
        code="hosted_framework_not_supported",
    )


def build_repo_bootstrap_plan(profile: FrameworkRuntimeProfile, workspace: Path) -> RepoBootstrapPlan:
    """Map detection profile to a concrete bootstrap plan (npm first for Playwright)."""
    root = workspace.resolve()

    if profile.framework_name == "playwright":
        lock = root / "package-lock.json"
        pkg = root / "package.json"
        if lock.is_file():
            cmd: list[str] | None = ["npm", "ci"]
            strat = "npm_ci"
            note = "package-lock.json present -> npm ci"
            paths = ("package.json", "package-lock.json", "node_modules", "node_modules/@playwright/test")
        elif pkg.is_file():
            cmd = ["npm", "install"]
            strat = "npm_install"
            note = "package.json only -> npm install"
            paths = ("package.json", "node_modules", "node_modules/@playwright/test")
        else:
            return RepoBootstrapPlan(
                command=None,
                required=False,
                validation_paths=(),
                notes="Playwright profile but package.json missing; cannot run npm bootstrap.",
                strategy_key="npm_none",
            )
        return RepoBootstrapPlan(
            command=cmd,
            required=True,
            validation_paths=paths,
            notes=note,
            strategy_key=strat,
        )

    # Other frameworks: defined for future wiring (hosted path raises before this for unsupported).
    if profile.bootstrap_strategy in ("mvn_compile_skip_tests", "gradle_test_classes", "pip_install_requirements"):
        return RepoBootstrapPlan(
            command=None,
            required=False,
            validation_paths=(),
            notes=f"Bootstrap strategy {profile.bootstrap_strategy} not implemented for hosted.",
            strategy_key=profile.bootstrap_strategy,
        )

    return RepoBootstrapPlan(
        command=None,
        required=False,
        validation_paths=(),
        notes="No automatic bootstrap for detected profile.",
        strategy_key="none",
    )


def _validate_playwright_npm_layout(
    workspace: Path,
    *,
    npm_command: list[str] | None,
    npm_exit_code: int | None,
) -> RuntimeValidationResult:
    ws = workspace.resolve()
    checks: list[str] = []

    def _fail(msg: str, *, path: str) -> None:
        logger.warning(
            "runtime_validation_failed",
            extra={
                "bootstrap_cwd": str(ws),
                "npm_command": npm_command,
                "npm_exit_code": npm_exit_code,
                "validation_path_checked": path,
                "validation_success": False,
                "framework_name": "playwright",
            },
        )
        raise RuntimeValidationError(msg, code="runtime_validation_failed")

    pkg = ws / "package.json"
    checks.append(str(pkg))
    if not pkg.is_file():
        _fail(
            f"package.json missing under hosted workspace {ws}.",
            path=str(pkg),
        )

    nm = ws / "node_modules"
    checks.append(str(nm))
    if not nm.is_dir():
        _fail(
            f"After {' '.join(npm_command or [])}, node_modules is missing or not a directory under {ws}.",
            path=str(nm),
        )

    pwt = nm / "@playwright" / "test"
    checks.append(str(pwt))
    if not pwt.is_dir():
        _fail(
            "Playwright workspace is missing node_modules/@playwright/test after npm; "
            "execution would fail resolving @playwright/test.",
            path=str(pwt),
        )

    logger.info(
        "runtime_validation_succeeded",
        extra={
            "bootstrap_cwd": str(ws),
            "npm_command": npm_command,
            "npm_exit_code": npm_exit_code,
            "validation_paths_checked": checks,
            "validation_success": True,
            "framework_name": "playwright",
        },
    )
    return RuntimeValidationResult(
        success=True,
        checks_run=tuple(checks),
        missing_requirements=(),
        notes="Playwright Node layout validated.",
    )


def validate_runtime_after_bootstrap(
    workspace: Path,
    profile: FrameworkRuntimeProfile,
    bootstrap_result: RepoBootstrapResult,
) -> RuntimeValidationResult:
    """Run profile-specific post-bootstrap checks (raises RuntimeValidationError on failure)."""
    if profile.framework_name != "playwright":
        return RuntimeValidationResult(
            success=True,
            checks_run=(),
            missing_requirements=(),
            notes="No hosted runtime validation implemented for this framework.",
        )

    if profile.runtime_validation_strategy != "playwright_npm_layout":
        return RuntimeValidationResult(
            success=True,
            checks_run=(),
            missing_requirements=(),
            notes="Unexpected validation strategy for Playwright; skipping strict layout checks.",
        )

    if not bootstrap_result.bootstrap_required:
        # npm skipped (should not happen for hosted Playwright with package.json)
        raise RuntimeValidationError(
            "Hosted Playwright requires npm bootstrap but bootstrap was skipped.",
            code="runtime_validation_failed",
        )

    ec = int(bootstrap_result.exit_code) if bootstrap_result.exit_code is not None else 0
    return _validate_playwright_npm_layout(
        workspace,
        npm_command=bootstrap_result.command,
        npm_exit_code=ec,
    )


def prepare_hosted_materialized_execution(
    workspace: Path,
    *,
    settings: Settings | None = None,
    subprocess_runner: Callable[..., dict[str, Any]] | None = None,
) -> HostedExecutionPreparation:
    """
    Hosted-only: detect framework, enforce support policy, npm bootstrap, runtime validation.

    Uses the same resolved workspace directory for npm cwd and validation as execution (repo root).
    """
    s = settings or get_settings()
    root = workspace.resolve()

    profile = detect_framework_runtime(root)
    logger.info(
        "framework_runtime_detected",
        extra={"framework_name": profile.framework_name, "bootstrap_strategy": profile.bootstrap_strategy},
    )

    assert_hosted_framework_supported(profile)

    plan = build_repo_bootstrap_plan(profile, root)
    logger.info(
        "repo_bootstrap_plan",
        extra={
            "framework_name": profile.framework_name,
            "plan_strategy": plan.strategy_key,
            "command": plan.command,
            "required": plan.required,
        },
    )

    if profile.framework_name == "playwright" and not (root / "package.json").is_file():
        raise RuntimeValidationError(
            "Hosted Playwright requires package.json at the repository root.",
            code="runtime_validation_failed",
        )

    res = bootstrap_node_workspace(
        root,
        workspace_profile="hosted_materialized",
        settings=s,
        subprocess_runner=subprocess_runner,
    )

    runtime = validate_runtime_after_bootstrap(root, profile, res)
    return HostedExecutionPreparation(profile=profile, bootstrap_result=res, runtime_validation=runtime)
