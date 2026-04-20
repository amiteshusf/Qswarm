"""Framework detector, adapters, and scan service (filesystem, no network)."""

from pathlib import Path

import pytest

from app.adapters.framework.detector import get_adapter_for_repo
from app.adapters.framework.playwright_adapter import PlaywrightAdapter
from app.core.constants import AutomationJobStatus
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path, scan_local_repo


def _write_playwright_repo(root: Path) -> None:
    (root / "playwright.config.ts").write_text("import { defineConfig } from '@playwright/test';\nexport default defineConfig({});\n")
    (root / "package.json").write_text(
        '{"name":"fake","devDependencies":{"@playwright/test":"^1.42.0"}}'
    )
    (root / "package-lock.json").write_text("{}")
    auth = root / "tests" / "auth"
    auth.mkdir(parents=True)
    (auth / "login.spec.ts").write_text("import { test } from '@playwright/test';\ntest('x', async () => {});\n")
    (root / "pages").mkdir()
    (root / "pages" / "LoginPage.ts").write_text("export class LoginPage {}\n")
    util = root / "utils"
    util.mkdir()
    (util / "helpers.ts").write_text("export const x = 1;\n")
    fx = root / "tests" / "fixtures"
    fx.mkdir(parents=True)
    (fx / "auth.fixture.ts").write_text("export const auth = {};\n")


def test_playwright_detect_via_config_and_package_json(tmp_path: Path):
    _write_playwright_repo(tmp_path)
    adapter = get_adapter_for_repo(tmp_path)
    assert adapter.name == "playwright"
    assert PlaywrightAdapter().detect(tmp_path) is True


def test_playwright_scan_summary_shape(tmp_path: Path):
    _write_playwright_repo(tmp_path)
    summary = scan_local_repo(tmp_path)
    assert summary["framework_type"] == "playwright"
    assert summary["language"] == "typescript"
    assert summary["package_manager"] == "npm"
    assert "playwright.config.ts" in summary["config_files"]
    assert summary["test_root"] == "tests"
    assert "npx playwright test" in summary["runner_command"]
    assert any("login.spec.ts" in p for p in summary["similar_test_files"])
    assert "pages" in summary["page_object_dirs"] or any(
        "pages" in d for d in summary["page_object_dirs"]
    )


def test_unknown_repo_gets_unknown_adapter(tmp_path: Path):
    (tmp_path / "README.md").write_text("# not a test repo\n")
    adapter = get_adapter_for_repo(tmp_path)
    assert adapter.name == "unknown"
    summary = scan_local_repo(tmp_path)
    assert summary["framework_type"] == "unknown"
    assert summary["missing_capabilities"]


def test_resolve_repo_path_missing():
    with pytest.raises(FrameworkScanError) as ei:
        resolve_repo_path(None)
    assert ei.value.code == "repo_path_required"
    with pytest.raises(FrameworkScanError) as ei2:
        resolve_repo_path("   ")
    assert ei2.value.code == "repo_path_required"


def test_resolve_repo_path_not_found(tmp_path: Path):
    p = tmp_path / "nope"
    with pytest.raises(FrameworkScanError) as ei:
        resolve_repo_path(str(p))
    assert ei.value.code == "repo_path_not_found"


def test_resolve_repo_path_not_directory(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(FrameworkScanError) as ei:
        resolve_repo_path(str(f))
    assert ei.value.code == "repo_path_not_a_directory"
