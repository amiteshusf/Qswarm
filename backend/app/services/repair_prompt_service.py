"""Assemble provider payload for repair / suggest_repair."""

from __future__ import annotations

from typing import Any

from app.db.models.automation_job import AutomationJob


def build_repair_payload(job: AutomationJob, failure_analysis: dict[str, Any]) -> dict[str, Any]:
    """Stable contract for future LLM repair integrations."""
    return {
        "job_id": str(job.id),
        "approved_case_id": job.approved_case_id,
        "repo_path": job.repo_path,
        "framework_summary": job.framework_summary_json or {},
        "case_spec": job.case_spec_json or {},
        "repo_context": job.repo_context_json or {},
        "change_plan": job.change_plan_json or {},
        "generated_patch_metadata": job.generated_patch_json or {},
        "execution_result": job.execution_result_json or {},
        "failure_analysis": failure_analysis,
        "repair_constraints": {
            "only_paths_in_plan_create_or_modify": True,
            "subset_of_planned_paths_allowed": True,
            "max_files": 8,
            "prefer_minimal_diff": True,
            "never_touch_files_to_avoid": True,
            "single_repair_attempt": True,
        },
    }
