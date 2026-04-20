"""Change planning: stub provider, validation, orchestration."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.constants import AutomationJobStatus
from app.db.models.automation_job import AutomationJob
from app.providers.coding.stub_provider import StubCodingProvider
from app.services.change_planning_service import (
    PlanningValidationError,
    create_validated_change_plan,
    validate_change_plan,
)
from app.services.planning_prompt_service import build_planning_payload


class BrokenPlanProvider:
    @property
    def name(self) -> str:
        return "broken"

    def create_change_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"framework_type": "playwright"}

    def generate_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"skipped": True}

    def suggest_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"skipped": True}

    def revise_after_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"skipped": True}


def _minimal_job(**kwargs: Any) -> AutomationJob:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "approved_case_id": "C-1",
        "workflow_run_id": None,
        "repo_id": None,
        "repo_path": "/tmp/repo",
        "base_branch": "main",
        "branch_name": None,
        "requested_by": "t",
        "status": AutomationJobStatus.PLANNING_CHANGES.value,
        "blocked_reason": None,
        "latest_attempt_number": 0,
        "framework_summary_json": {"framework_type": "playwright", "test_root": "tests"},
        "case_spec_json": {
            "title": "OTP reset",
            "objective": "reset",
            "steps": ["enter OTP"],
            "expected_results": ["ok"],
        },
        "repo_context_json": {
            "framework_type": "playwright",
            "similar_test_files": ["tests/auth/forgot-password.spec.ts"],
            "related_page_objects": ["pages/ForgotPasswordPage.ts"],
            "helper_files": ["utils/mailhog.ts"],
            "fixture_files": ["tests/fixtures/auth.fixture.ts"],
            "config_files": ["playwright.config.ts"],
        },
        "change_plan_json": None,
        "final_result_json": None,
        "case_input_json": None,
    }
    defaults.update(kwargs)
    return AutomationJob(**defaults)


def test_stub_provider_returns_valid_plan_shape():
    job = _minimal_job()
    payload = build_planning_payload(job)
    plan = StubCodingProvider().create_change_plan(payload)
    assert plan["framework_type"] == "playwright"
    assert plan["action_on_target_test_file"] in ("create", "modify")
    assert isinstance(plan["files_to_create"], list)
    assert isinstance(plan["files_to_modify"], list)
    assert isinstance(plan["files_to_reuse"], list)
    assert plan["target_test_file"]
    assert isinstance(plan["planning_rationale"], list) and plan["planning_rationale"]


def test_stub_prefers_forgot_password_and_mailhog_for_otp_case():
    job = _minimal_job()
    plan = StubCodingProvider().create_change_plan(build_planning_payload(job))
    assert "forgot-password" in plan["target_test_file"]
    reuse = [x.lower() for x in plan["files_to_reuse"]]
    assert any("mailhog" in x for x in reuse)
    mod = [x.lower() for x in plan["files_to_modify"]]
    assert any("forgot" in x for x in mod)


def test_validate_change_plan_rejects_path_traversal():
    job = _minimal_job()
    plan = StubCodingProvider().create_change_plan(build_planning_payload(job))
    plan = dict(plan)
    plan["files_to_modify"] = ["../etc/passwd"]
    ok, msg = validate_change_plan(plan, job)
    assert not ok
    assert "path" in msg.lower() or "invalid" in msg.lower()


def test_validate_rejects_framework_type_mismatch():
    job = _minimal_job()
    plan = StubCodingProvider().create_change_plan(build_planning_payload(job))
    plan = dict(plan)
    plan["framework_type"] = "cypress"
    ok, msg = validate_change_plan(plan, job)
    assert not ok
    assert "framework" in msg.lower()


def test_create_validated_change_plan_round_trip(db_session: Session):
    job = _minimal_job()
    db_session.add(job)
    db_session.flush()
    plan = create_validated_change_plan(db_session.get(AutomationJob, job.id))
    assert plan["files_to_avoid"] is not None
    assert isinstance(plan["scope_notes"], list)


def test_create_validated_change_plan_raises_on_invalid(db_session: Session):
    job = _minimal_job()
    db_session.add(job)
    db_session.flush()
    j = db_session.get(AutomationJob, job.id)

    def bad(_payload):
        return {"framework_type": "playwright"}

    with pytest.raises(PlanningValidationError):
        create_validated_change_plan(j, provider=BrokenPlanProvider())
