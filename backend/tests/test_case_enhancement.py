"""Case enhancement agent/service tests."""

from app.agents.case_enhancement_agent import run_case_enhancement
from app.services.case_enhancement_service import build_case_spec_from_job
from app.db.models.automation_job import AutomationJob


def test_case_enhancement_full_input():
    spec = run_case_enhancement(
        "CASE-123",
        case_title="Reset password with valid OTP",
        case_description="User receives OTP and sets a new password.",
        preconditions=["user account exists", "email inbox reachable"],
        steps=["open forgot password", "enter email", "enter OTP", "set new password"],
        expected_results=["OTP accepted", "login works with new password"],
    )
    assert spec["approved_case_id"] == "CASE-123"
    assert spec["title"] == "Reset password with valid OTP"
    assert spec["description"] == "User receives OTP and sets a new password."
    assert len(spec["steps"]) == 4
    assert spec["missing_information"] == []


def test_case_enhancement_minimal_input_missing_information():
    spec = run_case_enhancement("CASE-MIN")
    assert spec["title"] == "Automation case CASE-MIN"
    assert "steps not provided" in spec["missing_information"]
    assert "expected_results not provided" in spec["missing_information"]


def test_build_case_spec_from_job_uses_case_input_json():
    job = AutomationJob(
        approved_case_id="X-1",
        requested_by="u",
        base_branch="main",
        status="pending",
        case_input_json={
            "case_title": "  Login  ",
            "steps": [" click home "],
            "expected_results": [],
        },
    )
    spec = build_case_spec_from_job(job)
    assert spec["title"] == "Login"
    assert spec["steps"] == ["click home"]
    assert "expected_results not provided" in spec["missing_information"]
