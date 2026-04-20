"""Stub patch generation shape."""

from __future__ import annotations

import uuid

from app.core.constants import AutomationJobStatus
from app.db.models.automation_job import AutomationJob
from app.providers.coding.stub_provider import StubCodingProvider
from app.services.generation_prompt_service import build_generation_payload


def test_stub_generate_patch_structure():
    plan = {
        "framework_type": "playwright",
        "target_test_file": "tests/smoke.spec.ts",
        "action_on_target_test_file": "modify",
        "files_to_create": [],
        "files_to_modify": ["tests/smoke.spec.ts"],
        "files_to_reuse": [],
        "files_to_avoid": [],
        "planning_rationale": ["x"],
    }
    job = AutomationJob(
        id=uuid.uuid4(),
        approved_case_id="C",
        workflow_run_id=None,
        repo_id=None,
        repo_path=None,
        base_branch="main",
        branch_name=None,
        requested_by="t",
        status=AutomationJobStatus.GENERATING_CODE.value,
        blocked_reason=None,
        latest_attempt_number=0,
        framework_summary_json={"framework_type": "playwright"},
        case_input_json=None,
        case_spec_json={"title": "Smoke"},
        repo_context_json={"similar_test_files": []},
        change_plan_json=plan,
        generated_patch_json=None,
        final_result_json=None,
    )
    payload = build_generation_payload(job)
    patch = StubCodingProvider().generate_patch(payload)
    assert patch["framework_type"] == "playwright"
    assert patch["target_test_file"] == "tests/smoke.spec.ts"
    assert len(patch["generated_files"]) == 1
    assert patch["generated_files"][0]["action"] == "modify"
    assert "```" not in patch["generated_files"][0]["content"]
