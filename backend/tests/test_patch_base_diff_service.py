"""Tests for patch-vs-base comparison before session create-pr."""

from __future__ import annotations

import uuid

import pytest

from app.services.patch_base_diff_service import compare_patch_files_to_base
from app.services.workspace_cache_service import reapply_current_patch_for_pr_commit
from app.source_control.errors import SourceControlConfigurationError, SourceControlRepoError
from test_automation_jobs import _ensure_git_repo_for_session_pr, _playwright_fixture_repo


def test_compare_patch_files_identical_to_main(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _playwright_fixture_repo(root)
    _ensure_git_repo_for_session_pr(root)
    spec = root / "tests" / "smoke.spec.ts"
    content = spec.read_text(encoding="utf-8")
    files = [{"path": "tests/smoke.spec.ts", "action": "modify", "content": content}]

    comparison = compare_patch_files_to_base(root, "main", files)
    assert comparison.all_patch_files_match_base
    assert comparison.identical_paths == ("tests/smoke.spec.ts",)
    assert not comparison.differing_paths
    assert not comparison.new_paths


def test_compare_patch_files_differs_from_main(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _playwright_fixture_repo(root)
    _ensure_git_repo_for_session_pr(root)
    files = [{"path": "tests/smoke.spec.ts", "action": "modify", "content": "// changed\n"}]

    comparison = compare_patch_files_to_base(root, "main", files)
    assert comparison.has_net_diff_against_base
    assert comparison.differing_paths == ("tests/smoke.spec.ts",)
    assert not comparison.all_patch_files_match_base


def test_compare_patch_new_file_not_on_main(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _playwright_fixture_repo(root)
    _ensure_git_repo_for_session_pr(root)
    files = [{"path": "tests/brand-new.spec.ts", "action": "create", "content": "test('x', () => {});\n"}]

    comparison = compare_patch_files_to_base(root, "main", files)
    assert comparison.new_paths == ("tests/brand-new.spec.ts",)
    assert comparison.has_net_diff_against_base


def test_reapply_identical_patch_raises_pr_patch_identical_to_base(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _playwright_fixture_repo(root)
    _ensure_git_repo_for_session_pr(root)
    spec = root / "tests" / "smoke.spec.ts"
    content = spec.read_text(encoding="utf-8")
    files = [{"path": "tests/smoke.spec.ts", "action": "modify", "content": content}]

    with pytest.raises(SourceControlConfigurationError) as ei:
        reapply_current_patch_for_pr_commit(
            root,
            files,
            patch_version_id=uuid.uuid4(),
            patch_version_number=3,
            target_branch="main",
        )
    assert ei.value.code == "pr_patch_identical_to_base"
    assert "patch version 3" in ei.value.message
    assert "identical to base branch" in ei.value.message
    assert "no pull request is needed" in ei.value.message
    assert "tests/smoke.spec.ts" in ei.value.message


def test_reapply_differing_patch_produces_working_tree_diff(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _playwright_fixture_repo(root)
    _ensure_git_repo_for_session_pr(root)
    files = [{"path": "tests/smoke.spec.ts", "action": "modify", "content": "// changed for pr\n"}]

    out = reapply_current_patch_for_pr_commit(
        root,
        files,
        patch_version_id=uuid.uuid4(),
        patch_version_number=2,
        target_branch="main",
    )
    assert out["has_working_tree_diff"] is True
    assert out["base_comparison"]["differing_paths"] == ["tests/smoke.spec.ts"]


def test_inventory_style_patch_equals_main_like_production(tmp_path):
    """Mirrors hosted session where hydrated patch body matches origin/main exactly."""
    root = tmp_path / "pw-demo"
    root.mkdir()
    tests = root / "tests"
    tests.mkdir()
    inventory = tests / "inventory.spec.ts"
    main_body = (
        "import { test, expect } from '@playwright/test';\n"
        "test('inventory loads', async ({ page }) => {\n"
        "  await page.goto('/inventory');\n"
        "});\n"
    )
    inventory.write_text(main_body, encoding="utf-8")
    (root / "playwright.config.ts").write_text("export default {};\n")
    _ensure_git_repo_for_session_pr(root)

    files = [{"path": "tests/inventory.spec.ts", "action": "modify", "content": main_body}]
    comparison = compare_patch_files_to_base(root, "main", files)
    assert comparison.all_patch_files_match_base

    with pytest.raises(SourceControlConfigurationError) as ei:
        reapply_current_patch_for_pr_commit(
            root,
            files,
            patch_version_id=uuid.uuid4(),
            patch_version_number=3,
            target_branch="main",
        )
    assert ei.value.code == "pr_patch_identical_to_base"
    assert "inventory.spec.ts" in ei.value.message
