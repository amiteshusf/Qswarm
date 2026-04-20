"""
Story Intake Agent: maps a Jira-shaped story dict to a normalized intake artifact.

Sprint 1 uses deterministic extraction/heuristics (no LLM). Replace `run_intake`
with an LLM-backed implementation when ready.
"""

from __future__ import annotations

import re
from typing import Any


def _split_bullets(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    lines = re.split(r"[\n•\-\*]+", text)
    return [ln.strip() for ln in lines if ln.strip() and len(ln.strip()) > 2][:20]


def _infer_criteria(summary: str, description: str) -> list[dict[str, str]]:
    blob = f"{summary}\n{description}".lower()
    criteria: list[dict[str, str]] = []

    for phrase in ("must ", "should ", "user can ", "system shall ", "ensure "):
        if phrase in blob:
            idx = blob.find(phrase)
            snippet = (summary + "\n" + description)[
                max(0, idx - 20) : min(len(summary) + len(description) + 1, idx + 120)
            ]
            criteria.append(
                {
                    "text": snippet.strip().replace("\n", " "),
                    "source": "inferred_from_description",
                }
            )

    if not criteria:
        criteria.append(
            {
                "text": f"Core behavior described in summary is verifiable end-to-end: {summary[:200]}",
                "source": "summary_fallback",
            }
        )
    return criteria[:8]


def run_intake(jira_fields: dict[str, Any]) -> dict[str, Any]:
    """
    Build structured intake from normalized Jira fields.

    Expected keys: issue_key, summary, description, labels, priority, issue_type, status.
    """
    key = str(jira_fields.get("issue_key") or "UNKNOWN")
    summary = str(jira_fields.get("summary") or "").strip()
    description = str(jira_fields.get("description") or "").strip()
    labels = jira_fields.get("labels") or []
    if not isinstance(labels, list):
        labels = []
    priority = str(jira_fields.get("priority") or "unspecified")
    issue_type = str(jira_fields.get("issue_type") or "Story")

    scope_hints = _split_bullets(description)
    in_scope = scope_hints[:6] if scope_hints else [f"Deliver functionality described in: {summary or key}"]
    out_scope = [
        "Production data migration not covered unless explicitly stated",
        "Non-functional soak testing beyond agreed environments",
    ]
    if issue_type.lower() == "bug":
        out_scope.append("Root-cause analysis beyond fix verification")

    assumptions = [
        f"Issue type treated as: {issue_type}",
        f"Priority context: {priority}",
        "Jira description is the authoritative functional narrative for Sprint 1",
    ]
    if labels:
        assumptions.append(f"Labels considered as themes: {', '.join(str(x) for x in labels[:10])}")

    risks = []
    if "urgent" in priority.lower() or "highest" in priority.lower():
        risks.append("High priority may imply time pressure on test depth")
    if not description:
        risks.append("Sparse description increases interpretation risk for acceptance criteria")
    if not risks:
        risks.append("Standard delivery risk: scope clarification may arrive late")

    open_questions = []
    if not description:
        open_questions.append("Can we attach acceptance examples or links to design specs?")
    open_questions.append("Which environments and roles are in scope for validation?")

    return {
        "story_key": key,
        "business_goal": summary
        or f"Validate and ship changes for {key} with clear acceptance evidence.",
        "in_scope": in_scope,
        "out_of_scope": out_scope,
        "assumptions": assumptions,
        "risks": risks,
        "open_questions": open_questions,
        "testable_acceptance_criteria": _infer_criteria(summary, description),
        "recommended_test_focus": [
            "Happy-path user journey aligned to summary",
            "Boundary states implied by description bullets",
            "Regression around touched components (when known)",
        ],
    }
