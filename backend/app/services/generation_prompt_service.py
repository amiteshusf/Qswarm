"""Assemble structured payload for coding providers (code generation phase)."""

from __future__ import annotations

from typing import Any

from app.db.models.automation_job import AutomationJob


def build_generation_payload(job: AutomationJob) -> dict[str, Any]:
    """
    Build the context passed to ``CodeIntelligenceProvider.generate_patch``.

    Stable contract for future Codex/Claude integrations.
    """
    return {
        "job_id": str(job.id),
        "approved_case_id": job.approved_case_id,
        "repo_path": job.repo_path,
        "framework_summary": job.framework_summary_json or {},
        "case_spec": job.case_spec_json or {},
        "repo_context": job.repo_context_json or {},
        "change_plan": job.change_plan_json or {},
        "generation_constraints": {
            "only_paths_in_plan_create_or_modify": True,
            "prefer_minimal_edits": True,
            "respect_files_to_reuse_no_writes": True,
            "never_touch_files_to_avoid": True,
            "return_full_file_content_only": True,
            "no_markdown_fences": True,
            "max_generated_files": 20,
        },
    }
