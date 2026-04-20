"""Assemble provider payload for post-review revision (``revise_after_review``)."""

from __future__ import annotations

from typing import Any

from app.db.models.automation_job import AutomationJob


def build_review_revision_payload(job: AutomationJob, reviewer_instruction: str) -> dict[str, Any]:
    """Stable contract for future LLM revision integrations."""
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
        "reviewer_instruction": reviewer_instruction.strip(),
        "revision_constraints": {
            "only_paths_in_plan_create_or_modify": True,
            "subset_of_planned_paths_allowed": True,
            "never_touch_files_to_avoid": True,
            "prefer_minimal_diff": True,
            "prefer_existing_fixtures_and_helpers": True,
        },
    }
