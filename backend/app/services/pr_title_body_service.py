"""Deterministic PR title and body for automation jobs."""

from __future__ import annotations

import uuid
from typing import Any

from app.db.models.automation_job import AutomationJob
from app.services.execution_service import resolve_target_test_file


def build_pr_title_and_body(job: AutomationJob) -> tuple[str, str]:
    """Return ``(title, body)`` for the GitHub PR from job metadata."""
    case = job.case_spec_json if isinstance(job.case_spec_json, dict) else {}
    title_hint = str(case.get("title") or "").strip()
    case_id = job.approved_case_id.strip()
    fw = "unknown"
    if isinstance(job.framework_summary_json, dict):
        fw = str(job.framework_summary_json.get("framework_type") or fw)
    target = resolve_target_test_file(job) or "(unknown target)"

    if title_hint:
        pr_title = f"test: automate {case_id} — {title_hint}"[:500]
    else:
        pr_title = f"test: automate {case_id}"[:500]

    body_lines = [
        "## QSwarm automation",
        "",
        f"- **Automation job id:** `{job.id}`",
        f"- **Approved case id:** `{case_id}`",
        f"- **Framework:** `{fw}`",
        f"- **Target test file:** `{target}`",
        f"- **Base branch:** `{job.base_branch}`",
        "",
        "This branch was refreshed from the latest base before PR creation (merge, not rebase).",
        "",
        "**Merge and review are manual** — QSwarm does not auto-merge.",
        "",
        "_Generated automation; human review required before merge._",
    ]
    pr_body = "\n".join(body_lines)[:65000]
    return pr_title, pr_body
