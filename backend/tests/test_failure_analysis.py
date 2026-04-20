"""Deterministic failure analysis heuristics."""

from app.services.failure_analysis_service import analyze_execution_failure


def test_selector_timeout_is_repairable():
    fa = analyze_execution_failure(
        {
            "success": False,
            "exit_code": 1,
            "stdout_tail": "",
            "stderr_tail": "Timeout 30000ms exceeded waiting for locator('[data-testid=otp]')",
            "notes": [],
        }
    )
    assert fa["failure_type"] == "selector_issue"
    assert fa["repairable"] is True
    assert fa["needs_human_input"] is False
    assert fa["suggested_action"] == "repair_patch"


def test_mailhog_environment_needs_human():
    fa = analyze_execution_failure(
        {
            "success": False,
            "exit_code": 1,
            "stderr_tail": "Error: connect ECONNREFUSED 127.0.0.1:8025 mailhog",
            "stdout_tail": "",
            "notes": [],
        }
    )
    assert fa["failure_type"] == "missing_environment_capability"
    assert fa["repairable"] is False
    assert fa["needs_human_input"] is True
    assert fa["suggested_action"] == "ask_human"
    assert "clarification_question" in fa


def test_tooling_launch_not_repairable():
    fa = analyze_execution_failure(
        {
            "success": False,
            "exit_code": None,
            "launch_error": "npx: not found",
            "stdout_tail": "",
            "stderr_tail": "",
            "notes": [],
        }
    )
    assert fa["failure_type"] == "tooling_or_launch_issue"
    assert fa["repairable"] is False


def test_import_issue_repairable():
    fa = analyze_execution_failure(
        {
            "success": False,
            "exit_code": 1,
            "stderr_tail": "Error: Cannot find module '../../helpers/x'",
            "stdout_tail": "",
            "notes": [],
        }
    )
    assert fa["failure_type"] == "import_or_path_issue"
    assert fa["repairable"] is True
