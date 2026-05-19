"""Framework runtime detection, hosted preparation policy, and Playwright validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.framework_runtime_errors import (
    PlaywrightBrowserPreparationError,
    RuntimeValidationError,
    UnsupportedHostedFrameworkError,
)
from app.services.framework_runtime_service import (
    HOSTED_FULLY_SUPPORTED,
    assert_hosted_framework_supported,
    build_repo_bootstrap_plan,
    detect_framework_runtime,
    prepare_hosted_materialized_execution,
    validate_runtime_after_bootstrap,
)
from app.services.repo_bootstrap_service import RepoBootstrapResult, bootstrap_node_workspace


def test_detect_playwright_via_config_and_deps(tmp_path: Path):
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "package.json").write_text('{"devDependencies":{"@playwright/test":"^1.0.0"}}')
    p = detect_framework_runtime(tmp_path)
    assert p.framework_name == "playwright"
    assert p.framework_family == "e2e_web"
    assert p.bootstrap_strategy == "npm_ci_or_install"


def test_detect_webdriverio(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"devDependencies":{"webdriverio":"^8"}}')
    (tmp_path / "wdio.conf.ts").write_text("exports.config = {};\n")
    p = detect_framework_runtime(tmp_path)
    assert p.framework_name == "webdriverio"


def test_detect_cypress(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"devDependencies":{"cypress":"^13"}}')
    (tmp_path / "cypress.config.ts").write_text("export default {};\n")
    p = detect_framework_runtime(tmp_path)
    assert p.framework_name == "cypress"


def test_detect_maven(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project></project>")
    p = detect_framework_runtime(tmp_path)
    assert p.framework_name == "selenium_maven"


def test_detect_gradle(tmp_path: Path):
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }")
    p = detect_framework_runtime(tmp_path)
    assert p.framework_name == "selenium_gradle"


def test_detect_python_pytest_signals(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    p = detect_framework_runtime(tmp_path)
    assert p.framework_name == "pytest_python"


def test_package_json_takes_priority_over_pyproject(tmp_path: Path):
    """Monorepo signal: Node markers win over loose Python files when package.json exists."""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "package.json").write_text("{}")
    p = detect_framework_runtime(tmp_path)
    assert p.framework_family == "node"
    assert p.framework_name == "unknown"


def test_node_bootstrap_plan_playwright_lockfile(tmp_path: Path):
    import json

    (tmp_path / "playwright.config.ts").write_text("x")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )
    prof = detect_framework_runtime(tmp_path)
    plan = build_repo_bootstrap_plan(prof, tmp_path)
    assert plan.command == ["npm", "ci"]
    assert plan.required is True
    assert "node_modules/@playwright/test" in plan.validation_paths


def test_node_bootstrap_plan_playwright_lockfile_unusable(tmp_path: Path):
    (tmp_path / "playwright.config.ts").write_text("x")
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text("{}")
    prof = detect_framework_runtime(tmp_path)
    plan = build_repo_bootstrap_plan(prof, tmp_path)
    assert plan.command == ["npm", "install"]
    assert plan.strategy_key == "npm_install_lock_unusable"


def test_node_bootstrap_plan_playwright_package_only(tmp_path: Path):
    (tmp_path / "playwright.config.ts").write_text("x")
    (tmp_path / "package.json").write_text("{}")
    prof = detect_framework_runtime(tmp_path)
    plan = build_repo_bootstrap_plan(prof, tmp_path)
    assert plan.command == ["npm", "install"]


def test_playwright_runtime_validation_success(tmp_path: Path):
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "package.json").write_text('{"devDependencies":{"@playwright/test":"^1.0.0"}}')
    (tmp_path / "package-lock.json").write_text("{}")
    nm = tmp_path / "node_modules" / "@playwright" / "test"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text("{}")
    prof = detect_framework_runtime(tmp_path)
    br = RepoBootstrapResult(
        detected_stack="node_npm_lockfile",
        bootstrap_required=True,
        command=["npm", "ci"],
        success=True,
        exit_code=0,
        duration_ms=1,
        stdout_tail="",
        stderr_tail="",
        notes="ok",
    )
    vr = validate_runtime_after_bootstrap(tmp_path, prof, br)
    assert vr.success is True
    assert "node_modules" in "".join(vr.checks_run)


def test_playwright_runtime_validation_missing_package(tmp_path: Path):
    from app.services.framework_runtime_models import FrameworkRuntimeProfile

    pw = FrameworkRuntimeProfile(
        framework_family="e2e_web",
        framework_name="playwright",
        language="ts",
        package_manager="npm",
        build_tool=None,
        bootstrap_strategy="npm_ci_or_install",
        runtime_validation_strategy="playwright_npm_layout",
        execution_strategy="playwright_npx",
        confidence="high",
        notes=(),
    )
    br = RepoBootstrapResult(
        detected_stack="x",
        bootstrap_required=True,
        command=["npm", "ci"],
        success=True,
        exit_code=0,
        duration_ms=1,
        stdout_tail="",
        stderr_tail="",
        notes="ok",
    )
    with pytest.raises(RuntimeValidationError) as ei:
        validate_runtime_after_bootstrap(tmp_path, pw, br)
    assert ei.value.code == "runtime_validation_failed"
    assert ei.value.details is not None
    assert ei.value.details.get("npm_exit_code") == 0
    assert "playwright_test_abs" in ei.value.details


def test_unsupported_hosted_raises_for_cypress(tmp_path: Path):
    (tmp_path / "cypress.config.ts").write_text("export default {};\n")
    (tmp_path / "package.json").write_text('{"devDependencies":{"cypress":"^13"}}')
    p = detect_framework_runtime(tmp_path)
    with pytest.raises(UnsupportedHostedFrameworkError) as ei:
        assert_hosted_framework_supported(p)
    assert ei.value.code == "hosted_framework_not_supported"


def test_hosted_playwright_prepare_blocks_when_validation_fails(tmp_path: Path):
    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "package.json").write_text('{"devDependencies":{"@playwright/test":"^1"}}')
    (tmp_path / "package-lock.json").write_text("{}")

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        if argv == ["npm", "--version"]:
            return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
        return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

    with pytest.raises(RuntimeValidationError):
        prepare_hosted_materialized_execution(
            tmp_path,
            settings=Settings(qswarm_bootstrap_timeout_seconds=60),
            subprocess_runner=fake_run,
        )


def test_prepare_hosted_bootstrap_cwd_matches_workspace(tmp_path: Path):
    import json

    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "package.json").write_text('{"devDependencies":{"@playwright/test":"^1"}}')
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        if argv == ["npm", "--version"]:
            return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
        if list(argv)[:4] == ["npx", "playwright", "install", "chromium"]:
            return {"exit_code": 0, "stdout": "chromium ok\n", "stderr": "", "duration_ms": 1, "timed_out": False}
        root = Path(cwd)
        (root / "node_modules").mkdir(parents=True, exist_ok=True)
        pwt = root / "node_modules" / "@playwright" / "test"
        pwt.mkdir(parents=True, exist_ok=True)
        (pwt / "package.json").write_text("{}")
        return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

    prep = prepare_hosted_materialized_execution(
        tmp_path,
        settings=Settings(qswarm_bootstrap_timeout_seconds=60),
        subprocess_runner=fake_run,
    )
    ws = str(tmp_path.resolve())
    assert prep.bootstrap_result.diagnostics is not None
    assert prep.bootstrap_result.diagnostics["npm_cwd"] == ws
    assert prep.bootstrap_result.diagnostics["resolved_workspace_path"] == ws
    assert prep.browser_preparation is not None
    assert prep.browser_preparation.success is True
    assert prep.browser_preparation.cwd == ws
    assert list(prep.browser_preparation.command) == ["npx", "playwright", "install", "chromium"]


def test_prepare_hosted_playwright_chromium_install_failure_raises(tmp_path: Path):
    import json

    (tmp_path / "playwright.config.ts").write_text("export default {};\n")
    (tmp_path / "package.json").write_text('{"devDependencies":{"@playwright/test":"^1"}}')
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "x", "version": "1.0.0"}}})
    )

    def fake_run(argv, *, cwd, timeout_seconds, env=None):
        if argv == ["npm", "--version"]:
            return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
        if list(argv)[:4] == ["npx", "playwright", "install", "chromium"]:
            return {"exit_code": 1, "stdout": "", "stderr": "download failed", "duration_ms": 1, "timed_out": False}
        root = Path(cwd)
        (root / "node_modules").mkdir(parents=True, exist_ok=True)
        pwt = root / "node_modules" / "@playwright" / "test"
        pwt.mkdir(parents=True, exist_ok=True)
        (pwt / "package.json").write_text("{}")
        return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

    with pytest.raises(PlaywrightBrowserPreparationError) as ei:
        prepare_hosted_materialized_execution(
            tmp_path,
            settings=Settings(qswarm_bootstrap_timeout_seconds=60),
            subprocess_runner=fake_run,
        )
    assert ei.value.code == "playwright_browser_prep_failed"


def test_hosted_webdriverio_prepare_raises_before_playwright_browser_install(tmp_path: Path, monkeypatch):
    calls: list[int] = []

    def spy(*_a, **_k):
        calls.append(1)
        raise AssertionError("browser install should not run for unsupported hosted stacks")

    monkeypatch.setattr(
        "app.services.framework_runtime_service.run_hosted_playwright_chromium_browser_install",
        spy,
    )
    (tmp_path / "package.json").write_text('{"devDependencies":{"webdriverio":"^8"}}')
    (tmp_path / "wdio.conf.ts").write_text("exports.config = {};\n")
    with pytest.raises(UnsupportedHostedFrameworkError):
        prepare_hosted_materialized_execution(
            tmp_path,
            settings=Settings(qswarm_bootstrap_timeout_seconds=60),
            subprocess_runner=lambda *a, **k: {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False},
        )
    assert calls == []


def test_playwright_in_hosted_supported_set():
    assert "playwright" in HOSTED_FULLY_SUPPORTED
