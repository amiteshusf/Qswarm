"""Deterministic test-design plan from requirement analysis (provider-swappable)."""

from __future__ import annotations

from typing import Any


def run_test_design_plan(analysis: dict[str, Any]) -> dict[str, Any]:
    """Build structured test-design plan JSON from requirement analysis."""
    story_key = str(analysis.get("story_key") or "UNKNOWN")
    scope = analysis.get("suggested_test_scope") or []
    if not isinstance(scope, list):
        scope = []
    ac = analysis.get("acceptance_criteria") or []
    if not isinstance(ac, list):
        ac = []

    functional_areas = [str(x) for x in scope[:8]] or ["Core user journey", "Error handling"]
    positive = [
        "Primary success path for the main user goal",
        "Happy-path validation for each major acceptance criterion",
    ]
    negative = [
        "Invalid input and authorization failures",
        "Boundary and malformed data handling",
    ]
    boundary = ["Upper/lower limits for key inputs and volumes"]
    automation_candidates = functional_areas[:3]

    return {
        "story_key": story_key,
        "summary": f"Test plan for {story_key}: cover success, failure, and edge behavior.",
        "functional_areas": functional_areas,
        "positive_scenarios": positive,
        "negative_scenarios": negative,
        "boundary_cases": boundary,
        "data_variations": ["Nominal data", "Empty/minimal data", "Maximum allowed values"],
        "roles_personas": ["Standard user", "Privileged user where applicable"],
        "integration_considerations": analysis.get("dependencies") or [],
        "out_of_scope": ["Performance soak testing", "Unrelated legacy modules"],
        "automation_candidates": automation_candidates,
        "expected_case_range": {"min": 3, "max": 8},
        "traceability": [{"acceptance_criterion": str(c), "planned_coverage": "positive"} for c in ac[:6]],
        "assumptions": analysis.get("assumptions") or [],
        "dependencies": analysis.get("dependencies") or [],
        "risks": analysis.get("risks") or [],
    }
