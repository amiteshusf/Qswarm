"""Case enhancement orchestration."""

from __future__ import annotations

from typing import Any

from app.agents.case_enhancement_agent import run_case_enhancement
from app.db.models.automation_job import AutomationJob


def build_case_spec_from_job(job: AutomationJob) -> dict[str, Any]:
    """Read optional inputs from ``job.case_input_json`` and produce ``case_spec_json`` shape."""
    raw: dict[str, Any] = job.case_input_json if isinstance(job.case_input_json, dict) else {}
    return run_case_enhancement(
        job.approved_case_id,
        case_title=raw.get("case_title"),
        case_description=raw.get("case_description"),
        preconditions=raw.get("preconditions"),
        steps=raw.get("steps"),
        expected_results=raw.get("expected_results"),
    )
