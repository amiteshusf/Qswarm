"""Repo context collection tests."""

from pathlib import Path

import pytest

from app.services.repo_context_service import RepoContextError, collect_repo_context


def _rich_playwright_fw() -> dict:
    return {
        "framework_type": "playwright",
        "test_root": "tests",
        "similar_test_files": [
            "tests/auth/login.spec.ts",
            "tests/auth/forgot-password.spec.ts",
        ],
        "page_object_dirs": ["pages"],
        "fixture_files": ["tests/fixtures/auth.fixture.ts"],
        "helper_dirs": ["utils"],
        "config_files": ["playwright.config.ts"],
    }


def _write_rich_repo(root: Path) -> None:
    for rel, content in [
        ("playwright.config.ts", "export default {}\n"),
        ("package.json", "{}"),
        ("tests/auth/login.spec.ts", "// login\n"),
        ("tests/auth/forgot-password.spec.ts", "// forgot\n"),
        ("pages/LoginPage.ts", "export class LoginPage {}\n"),
        ("pages/ForgotPasswordPage.ts", "export class FP {}\n"),
        ("utils/mailhog.ts", "export const mh = 1\n"),
        ("tests/fixtures/auth.fixture.ts", "export const fx = 1\n"),
    ]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def test_repo_context_collects_bounded_lists(tmp_path: Path):
    _write_rich_repo(tmp_path)
    fw = _rich_playwright_fw()
    case_spec = {
        "title": "Reset password with OTP",
        "objective": "Verify forgot password flow",
        "steps": ["submit email", "enter OTP", "reset password"],
        "expected_results": ["user can log in"],
        "preconditions": [],
    }
    ctx = collect_repo_context(tmp_path.resolve(), fw, case_spec)
    assert ctx["framework_type"] == "playwright"
    assert ctx["selected_test_root"] == "tests"
    assert len(ctx["similar_test_files"]) <= 8
    assert any("forgot-password" in f or "login" in f for f in ctx["similar_test_files"])
    assert len(ctx["related_page_objects"]) <= 8
    assert any("LoginPage" in f or "ForgotPassword" in f for f in ctx["related_page_objects"])
    assert "tests/fixtures/auth.fixture.ts" in ctx["fixture_files"] or ctx["fixture_files"]
    assert isinstance(ctx["relevance_notes"], list)


def test_repo_context_non_playwright_raises(tmp_path: Path):
    with pytest.raises(RepoContextError):
        collect_repo_context(
            tmp_path,
            {"framework_type": "cypress"},
            {"title": "x"},
        )
