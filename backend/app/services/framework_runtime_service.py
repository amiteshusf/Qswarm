"""
Framework / runtime detection and hosted execution preparation.

Pipeline for hosted materialized workspaces:
  detect -> bootstrap plan -> npm (when applicable) -> runtime validation ->
  Playwright browser install (chromium, hosted Playwright only) -> (execution plan at run time)

Playwright is the first fully supported hosted framework. Other stacks are detected and rejected
cleanly until incremental support is added.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.adapters.framework.playwright_adapter import PlaywrightAdapter
from app.automation_engine.cli_subprocess import run_subprocess_argv
from app.core.config import Settings, get_settings
from app.services.framework_runtime_models import (
    FrameworkRuntimeProfile,
    PlaywrightBrowserPreparationResult,
    RepoBootstrapPlan,
    RuntimeValidationResult,
)
from app.services.framework_runtime_errors import (
    FrameworkDetectionError,
    PlaywrightBrowserPreparationError,
    RuntimeValidationError,
    UnsupportedHostedFrameworkError,
)
from app.services.repo_bootstrap_service import (
    RepoBootstrapResult,
    bootstrap_node_workspace,
    package_lock_usable_for_npm_ci,
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
    """Outcome of hosted materialized detect + bootstrap + runtime validation + browser prep."""

    profile: FrameworkRuntimeProfile
    bootstrap_result: RepoBootstrapResult
    runtime_validation: RuntimeValidationResult
    browser_preparation: PlaywrightBrowserPreparationResult | None = None


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
        if lock.is_file() and package_lock_usable_for_npm_ci(root):
            cmd: list[str] | None = ["npm", "ci"]
            strat = "npm_ci"
            note = "package-lock.json usable for npm ci -> npm ci"
            paths = ("package.json", "package-lock.json", "node_modules", "node_modules/@playwright/test")
        elif lock.is_file():
            cmd = ["npm", "install"]
            strat = "npm_install_lock_unusable"
            note = "package-lock.json present but not usable for npm ci -> npm install"
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
    bootstrap_diagnostics: dict[str, Any] | None = None,
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> RuntimeValidationResult:
    ws = workspace.resolve()
    checks: list[str] = []
    pkg_abs = ws / "package.json"
    nm_abs = ws / "node_modules"
    pwt_abs = nm_abs / "@playwright" / "test"

    def _fail(msg: str, *, path: str) -> None:
        details: dict[str, Any] = {
            "resolved_workspace": str(ws),
            "npm_cwd": str(ws),
            "npm_command": npm_command,
            "npm_exit_code": npm_exit_code,
            "npm_reported_success": npm_exit_code == 0,
            "absolute_path_checked": path,
            "package_json_abs": str(pkg_abs),
            "node_modules_abs": str(nm_abs),
            "playwright_test_abs": str(pwt_abs),
            "package_lock_present": bootstrap_diagnostics.get("package_lock_present")
            if bootstrap_diagnostics
            else (ws / "package-lock.json").is_file(),
            "package_lock_usable_for_npm_ci": bootstrap_diagnostics.get("package_lock_usable_for_npm_ci")
            if bootstrap_diagnostics
            else None,
            "node_modules_exists_after": bootstrap_diagnostics.get("node_modules_exists_after")
            if bootstrap_diagnostics
            else nm_abs.is_dir(),
            "playwright_test_pkg_exists_after": bootstrap_diagnostics.get("playwright_test_pkg_exists_after")
            if bootstrap_diagnostics
            else pwt_abs.is_dir(),
            "hosted_bootstrap_env": bootstrap_diagnostics.get("hosted_bootstrap_env") if bootstrap_diagnostics else None,
            "stdout_tail_short": (stdout_tail or "")[:1500],
            "stderr_tail_short": (stderr_tail or "")[:1500],
        }
        hint = ""
        if npm_exit_code == 0 and bootstrap_diagnostics and bootstrap_diagnostics.get("hosted_bootstrap_env"):
            hint = (
                " If devDependencies were omitted (e.g. NODE_ENV=production), hosted bootstrap now forces "
                "NPM_CONFIG_PRODUCTION=false for materialized installs; verify install logs if this persists."
            )
        full_msg = (
            f"{msg} cwd={ws} npm={' '.join(npm_command or [])} exit={npm_exit_code} "
            f"checked={path} lock_present={details['package_lock_present']}"
            f"{hint}"
        )
        logger.warning(
            "runtime_validation_failed",
            extra={
                "bootstrap_cwd": str(ws),
                "npm_command": npm_command,
                "npm_exit_code": npm_exit_code,
                "validation_path_checked": path,
                "validation_success": False,
                "framework_name": "playwright",
                "package_lock_present": details["package_lock_present"],
                "node_modules_exists_after": details["node_modules_exists_after"],
            },
        )
        raise RuntimeValidationError(full_msg[:3900], code="runtime_validation_failed", details=details)

    pkg = pkg_abs
    checks.append(str(pkg))
    if not pkg.is_file():
        _fail(
            f"package.json missing under hosted workspace {ws}.",
            path=str(pkg),
        )

    nm = nm_abs
    checks.append(str(nm))
    if not nm.is_dir():
        _fail(
            f"After {' '.join(npm_command or [])}, node_modules is missing or not a directory under {ws}.",
            path=str(nm),
        )

    pwt = pwt_abs
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
        bootstrap_diagnostics=bootstrap_result.diagnostics,
        stdout_tail=bootstrap_result.stdout_tail or "",
        stderr_tail=bootstrap_result.stderr_tail or "",
    )


_HOSTED_PLAYWRIGHT_CHROMIUM_INSTALL_ARGV: tuple[str, ...] = ("npx", "playwright", "install", "chromium")


def _log_tail(text: str, max_chars: int = 6000) -> str:
    t = text or ""
    if len(t) <= max_chars:
        return t
    return "…" + t[-max_chars:]


def run_hosted_playwright_chromium_browser_install(
    workspace: Path,
    *,
    settings: Settings,
    subprocess_runner: Callable[..., dict[str, Any]] | None = None,
) -> PlaywrightBrowserPreparationResult:
    """
    Run ``npx playwright install chromium`` in the repo root (hosted materialized Playwright only).

    Uses explicit argv (no shell), same cwd as npm bootstrap and test execution.
    """
    ws = workspace.resolve()
    argv = list(_HOSTED_PLAYWRIGHT_CHROMIUM_INSTALL_ARGV)
    run = subprocess_runner or run_subprocess_argv
    timeout = float(settings.qswarm_playwright_browser_install_timeout_seconds)
    logger.info(
        "playwright_browser_prep_started",
        extra={"cwd": str(ws), "command": argv, "framework_name": "playwright"},
    )
    t0 = time.perf_counter()
    out = run(argv, cwd=ws, timeout_seconds=timeout, env=None)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    stdout_tail = _log_tail(str(out.get("stdout") or ""))
    stderr_tail = _log_tail(str(out.get("stderr") or ""))

    if out.get("timed_out"):
        details: dict[str, Any] = {
            "cwd": str(ws),
            "command": argv,
            "duration_ms": duration_ms,
            "stdout_tail": stdout_tail[:2000],
            "stderr_tail": stderr_tail[:2000],
        }
        msg = (
            f"{' '.join(argv)} timed out after {int(timeout)}s (cwd={ws}). "
            f"stderr tail: {_log_tail(str(out.get('stderr') or ''), 2500)}"
        )
        logger.warning(
            "playwright_browser_prep_failed",
            extra={"cwd": str(ws), "command": argv, "timed_out": True, "framework_name": "playwright"},
        )
        raise PlaywrightBrowserPreparationError(
            msg.strip(),
            code="playwright_browser_prep_timeout",
            details=details,
        )

    exit_code = out.get("exit_code")
    success = exit_code == 0

    if not success:
        details = {
            "cwd": str(ws),
            "command": argv,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "stdout_tail": stdout_tail[:2000],
            "stderr_tail": stderr_tail[:2000],
        }
        msg = (
            f"{' '.join(argv)} failed with exit {exit_code} (cwd={ws}). "
            f"stderr: {_log_tail(stderr_tail, 2500)} stdout: {_log_tail(stdout_tail, 1500)}"
        )
        logger.warning(
            "playwright_browser_prep_failed",
            extra={
                "cwd": str(ws),
                "command": argv,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "framework_name": "playwright",
            },
        )
        raise PlaywrightBrowserPreparationError(
            msg.strip(),
            code="playwright_browser_prep_failed",
            details=details,
        )

    logger.info(
        "playwright_browser_prep_completed",
        extra={
            "cwd": str(ws),
            "command": argv,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "framework_name": "playwright",
        },
    )
    return PlaywrightBrowserPreparationResult(
        command=_HOSTED_PLAYWRIGHT_CHROMIUM_INSTALL_ARGV,
        cwd=str(ws),
        success=True,
        exit_code=int(exit_code) if exit_code is not None else None,
        duration_ms=duration_ms,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        notes="npx playwright install chromium exited 0.",
    )


def prepare_hosted_materialized_execution(
    workspace: Path,
    *,
    settings: Settings | None = None,
    subprocess_runner: Callable[..., dict[str, Any]] | None = None,
) -> HostedExecutionPreparation:
    """
    Hosted-only: detect framework, enforce support policy, npm bootstrap, runtime validation,
    then Playwright Chromium install for Playwright workspaces.

    Uses the same resolved workspace directory for npm cwd, browser install cwd, and validation as execution.
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
    browser: PlaywrightBrowserPreparationResult | None = None
    if profile.framework_name == "playwright":
        browser = run_hosted_playwright_chromium_browser_install(
            root,
            settings=s,
            subprocess_runner=subprocess_runner,
        )
    return HostedExecutionPreparation(
        profile=profile,
        bootstrap_result=res,
        runtime_validation=runtime,
        browser_preparation=browser,
    )
