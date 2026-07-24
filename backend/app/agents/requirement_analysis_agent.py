"""Deterministic requirement analysis from story intake (provider-swappable)."""

from __future__ import annotations

from typing import Any


def run_requirement_analysis(intake: dict[str, Any]) -> dict[str, Any]:
    """Build structured requirement analysis JSON from story intake artifact."""
    story_key = str(intake.get("story_key") or "UNKNOWN")
    summary = str(intake.get("business_goal") or intake.get("summary") or "").strip()
    description = str(intake.get("description") or "").strip()
    criteria = intake.get("testable_acceptance_criteria") or []
    if not isinstance(criteria, list):
        criteria = []
    in_scope = intake.get("in_scope") or []
    if not isinstance(in_scope, list):
        in_scope = []
    assumptions = intake.get("assumptions") or []
    if not isinstance(assumptions, list):
        assumptions = []
    risks = intake.get("risks") or []
    if not isinstance(risks, list):
        risks = []

    ac_texts = []
    for c in criteria[:12]:
        if isinstance(c, dict):
            t = str(c.get("text") or "").strip()
        else:
            t = str(c).strip()
        if t:
            ac_texts.append(t)

    missing: list[str] = []
    if not description:
        missing.append("Detailed functional description is limited")
    if len(ac_texts) < 2:
        missing.append("Few explicit acceptance criteria were found")

    ambiguities: list[str] = []
    if "tbd" in description.lower() or "tbd" in summary.lower():
        ambiguities.append("Open TBD items in story text")

    return {
        "story_key": story_key,
        "story_summary": summary[:2000],
        "acceptance_criteria": ac_texts,
        "business_rules": [str(x) for x in in_scope[:8]],
        "missing_information": missing + [str(x) for x in intake.get("missing_information") or [] if x][:10],
        "ambiguities": ambiguities,
        "dependencies": [str(x) for x in intake.get("dependencies") or [] if x][:10],
        "risks": [str(x) for x in risks[:10]],
        "suggested_test_scope": in_scope[:10] or [summary[:300]] if summary else [],
        "assumptions": [str(x) for x in assumptions[:12]],
        "readiness": {
            "ready_for_planning": len(missing) == 0,
            "confidence": "high" if len(ac_texts) >= 2 and description else "medium",
            "blockers": missing[:5],
        },
    }
