"""Patch validation against approved change plans."""

from __future__ import annotations

import uuid

import pytest

from app.core.constants import AutomationJobStatus
from app.db.models.automation_job import AutomationJob
from app.services.patch_validation_service import PatchValidationError, validate_generated_patch


def _job_with_plan(**patch_plan: dict) -> AutomationJob:
    plan = {
        "framework_type": "playwright",
        "target_test_file": "tests/auth/forgot-password.spec.ts",
        "action_on_target_test_file": "modify",
        "files_to_create": [],
        "files_to_modify": [
            "tests/auth/forgot-password.spec.ts",
            "pages/ForgotPasswordPage.ts",
        ],
        "files_to_reuse": ["utils/mailhog.ts"],
        "files_to_avoid": ["playwright.config.ts"],
        "planning_rationale": ["r1"],
    }
    plan.update(patch_plan)
    return AutomationJob(
        id=uuid.uuid4(),
        approved_case_id="X",
        workflow_run_id=None,
        repo_id=None,
        repo_path="/tmp/r",
        base_branch="main",
        branch_name=None,
        requested_by="t",
        status=AutomationJobStatus.GENERATING_CODE.value,
        blocked_reason=None,
        latest_attempt_number=0,
        framework_summary_json={"framework_type": "playwright"},
        case_input_json=None,
        case_spec_json={"title": "T"},
        repo_context_json={"framework_type": "playwright"},
        change_plan_json=plan,
        generated_patch_json=None,
        final_result_json=None,
    )


def _valid_patch() -> dict:
    body = "// line\n"
    return {
        "framework_type": "playwright",
        "target_test_file": "tests/auth/forgot-password.spec.ts",
        "generated_files": [
            {"path": "tests/auth/forgot-password.spec.ts", "action": "modify", "content": body * 5},
            {"path": "pages/ForgotPasswordPage.ts", "action": "modify", "content": body * 5},
        ],
        "reused_files": ["utils/mailhog.ts"],
        "generation_notes": ["note"],
    }


def test_validate_accepts_in_scope_patch():
    validate_generated_patch(_valid_patch(), _job_with_plan())


def test_validate_rejects_unexpected_path():
    p = _valid_patch()
    p["generated_files"] = list(p["generated_files"])
    p["generated_files"].append(
        {"path": "tests/other.spec.ts", "action": "modify", "content": "// x\n" * 5}
    )
    with pytest.raises(PatchValidationError, match="scope|exactly"):
        validate_generated_patch(p, _job_with_plan())


def test_validate_rejects_path_listed_in_files_to_avoid():
    """Even if a path appears under modify in a bad plan, avoid wins."""
    job = _job_with_plan(
        files_to_modify=[
            "tests/auth/forgot-password.spec.ts",
            "pages/ForgotPasswordPage.ts",
        ],
        files_to_avoid=["pages/ForgotPasswordPage.ts"],
    )
    with pytest.raises(PatchValidationError, match="avoid"):
        validate_generated_patch(_valid_patch(), job)


def test_validate_rejects_wrong_action_for_path():
    p = _valid_patch()
    p["generated_files"] = [
        {"path": "tests/auth/forgot-password.spec.ts", "action": "create", "content": "// c\n" * 5},
        {"path": "pages/ForgotPasswordPage.ts", "action": "modify", "content": "// c\n" * 5},
    ]
    with pytest.raises(PatchValidationError, match="create not allowed|target_test_file action"):
        validate_generated_patch(p, _job_with_plan())


def test_validate_rejects_traversal():
    p = _valid_patch()
    p["generated_files"] = [
        {"path": "../evil.ts", "action": "modify", "content": "// c\n" * 5},
        {"path": "pages/ForgotPasswordPage.ts", "action": "modify", "content": "// c\n" * 5},
    ]
    with pytest.raises(PatchValidationError, match="invalid|scope|exactly"):
        validate_generated_patch(p, _job_with_plan())


def test_validate_rejects_empty_content():
    p = _valid_patch()
    p["generated_files"][0] = dict(p["generated_files"][0], content="   ")
    with pytest.raises(PatchValidationError, match="non-empty"):
        validate_generated_patch(p, _job_with_plan())


def test_validate_rejects_assistant_style_opening():
    p = _valid_patch()
    p["generated_files"][0] = dict(
        p["generated_files"][0],
        content="Here is the updated file\nconst x = 1;\n" * 3,
    )
    with pytest.raises(PatchValidationError, match="prose"):
        validate_generated_patch(p, _job_with_plan())


def test_validate_rejects_markdown_fence():
    p = _valid_patch()
    p["generated_files"][0] = dict(
        p["generated_files"][0],
        content="```ts\nconst x = 1;\n```\n",
    )
    with pytest.raises(PatchValidationError, match="markdown"):
        validate_generated_patch(p, _job_with_plan())


def test_validate_rejects_files_to_reuse_overlap():
    job = _job_with_plan(
        target_test_file="tests/auth/forgot-password.spec.ts",
        files_to_modify=[
            "tests/auth/forgot-password.spec.ts",
            "utils/mailhog.ts",
            "pages/ForgotPasswordPage.ts",
        ],
        files_to_reuse=["utils/mailhog.ts"],
    )
    patch = {
        "framework_type": "playwright",
        "target_test_file": "tests/auth/forgot-password.spec.ts",
        "generated_files": [
            {"path": "tests/auth/forgot-password.spec.ts", "action": "modify", "content": "// m\n" * 5},
            {"path": "utils/mailhog.ts", "action": "modify", "content": "// m\n" * 5},
            {"path": "pages/ForgotPasswordPage.ts", "action": "modify", "content": "// m\n" * 5},
        ],
    }
    with pytest.raises(PatchValidationError, match="reuse"):
        validate_generated_patch(patch, job)
