"""Repair patch validation (subset of planned paths allowed)."""

import uuid

import pytest

from app.core.constants import AutomationJobStatus
from app.db.models.automation_job import AutomationJob
from app.services.patch_validation_service import PatchValidationError, validate_repair_patch


def _job() -> AutomationJob:
    return AutomationJob(
        id=uuid.uuid4(),
        approved_case_id="R-1",
        workflow_run_id=None,
        repo_id=None,
        repo_path="/tmp/r",
        base_branch="main",
        branch_name=None,
        requested_by="t",
        status=AutomationJobStatus.FAILED.value,
        blocked_reason="x",
        latest_attempt_number=0,
        framework_summary_json={"framework_type": "playwright"},
        case_input_json=None,
        case_spec_json={},
        repo_context_json={},
        change_plan_json={
            "framework_type": "playwright",
            "target_test_file": "tests/a.spec.ts",
            "action_on_target_test_file": "modify",
            "files_to_create": [],
            "files_to_modify": ["tests/a.spec.ts", "pages/P.ts"],
            "files_to_reuse": [],
            "files_to_avoid": [],
            "planning_rationale": ["x"],
        },
        generated_patch_json=None,
        execution_result_json={"success": False},
        failure_analysis_json=None,
        repair_result_json=None,
        final_result_json=None,
    )


def test_repair_patch_subset_passes():
    patch = {
        "framework_type": "playwright",
        "target_test_file": "tests/a.spec.ts",
        "generated_files": [
            {"path": "tests/a.spec.ts", "action": "modify", "content": "// ok\n" * 5},
        ],
        "generation_notes": ["repair"],
    }
    validate_repair_patch(patch, _job())


def test_repair_patch_extra_path_rejected():
    patch = {
        "framework_type": "playwright",
        "target_test_file": "tests/a.spec.ts",
        "generated_files": [
            {"path": "tests/a.spec.ts", "action": "modify", "content": "// ok\n" * 5},
            {"path": "utils/other.ts", "action": "modify", "content": "// bad\n" * 5},
        ],
    }
    with pytest.raises(PatchValidationError, match="scope"):
        validate_repair_patch(patch, _job())
