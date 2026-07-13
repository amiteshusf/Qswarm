"""Unit tests for ``GET /api/v1/dashboard`` BFF normalization (``ui_v1_dashboard``)."""

from __future__ import annotations

import pytest

from app.services.ui_v1_dashboard import (
    empty_ui_session_counts,
    format_dashboard_json_for_ui,
    map_backend_to_ui_dashboard_status,
)


@pytest.mark.parametrize(
    "summary,expected",
    [
        (
            {"status": "approved_for_pr", "job_status": "approved_for_pr", "current_round_number": 1},
            "queued",
        ),
        ({"status": "pr_created", "job_status": "pr_created", "current_round_number": 1}, "succeeded"),
        ({"status": "pr_failed", "job_status": "pr_creation_failed", "current_round_number": 1}, "failed"),
        ({"status": "pending", "job_status": "pr_creation_failed", "current_round_number": 0}, "failed"),
        ({"status": "creating_pr", "job_status": "creating_pr", "current_round_number": 1}, "running"),
        (
            {"status": "executing", "job_status": "revising_after_review", "current_round_number": 2},
            "revising",
        ),
        ({"status": "pending", "job_status": "pending", "current_round_number": 0}, "draft"),
        ({"status": "pending", "job_status": "pending", "current_round_number": 1}, "queued"),
        ({"status": "planning", "job_status": "scanning_framework", "current_round_number": 1}, "running"),
        ({"status": "awaiting_review", "job_status": "awaiting_automation_review", "current_round_number": 1}, "awaiting_review"),
        ({"status": "failed", "job_status": "failed", "current_round_number": 1}, "failed"),
        ({"status": "cancelled", "job_status": None, "current_round_number": 0}, "cancelled"),
        ({"status": "weird_future_status", "job_status": None, "current_round_number": 0}, "queued"),
    ],
)
def test_map_backend_to_ui_dashboard_status(summary: dict, expected: str) -> None:
    assert map_backend_to_ui_dashboard_status(summary) == expected


def test_format_dashboard_json_for_ui_session_counts_keys_unchanged() -> None:
    counts = empty_ui_session_counts()
    counts["queued"] = 3
    out = format_dashboard_json_for_ui(
        {
            "session_counts": counts,
            "recent_sessions": [
                {
                    "id": "abc",
                    "status": "queued",
                    "engine": "stub",
                    "repo_connection_id": "",
                    "source_ref": "",
                    "approved_case_id": "",
                    "created_at": "",
                    "updated_at": "",
                    "job_status": "pending",
                    "current_round_number": 0,
                }
            ],
            "repository_connection_count": 0,
            "branch_policy_count": 0,
            "environment": "test",
            "application_name": "QSwarm",
        }
    )
    assert out["sessionCounts"] is counts
    assert set(out["sessionCounts"].keys()) == set(empty_ui_session_counts().keys())
    row = out["recentSessions"][0]
    assert row["repoConnectionId"] == ""
    assert row["sourceRef"] == ""
    assert row["status"] == "queued"
    assert row["engine"] == "stub"
    assert "repositoryConnectionCount" in out
