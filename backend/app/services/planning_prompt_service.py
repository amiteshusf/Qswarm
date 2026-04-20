"""Assemble structured payload for coding providers (planning phase)."""

from __future__ import annotations

from typing import Any

from app.db.models.automation_job import AutomationJob


def build_planning_payload(job: AutomationJob) -> dict[str, Any]:
    """
    Build the planning context passed to ``CodeIntelligenceProvider.create_change_plan``.

    Keeps a stable contract for future Codex/Claude integrations.
    """
    return {
        "job_id": str(job.id),
        "approved_case_id": job.approved_case_id,
        "framework_summary": job.framework_summary_json or {},
        "case_spec": job.case_spec_json or {},
        "repo_context": job.repo_context_json or {},
        "planning_constraints": {
            "prefer_minimal_changes": True,
            "reuse_existing_fixtures_and_helpers": True,
            "avoid_config_changes_unless_necessary": True,
            "max_files_to_touch": 15,
            "max_paths_per_category": 12,
        },
    }
