"""
Test Design Agent: draft scenarios from a story intake artifact.

Sprint 1 uses template-driven deterministic output (no LLM).
"""

from __future__ import annotations

from typing import Any


def run_test_design(intake: dict[str, Any]) -> dict[str, Any]:
    """Produce draft test design JSON from `run_intake` output shape."""
    story_key = str(intake.get("story_key") or "UNKNOWN")
    goal = str(intake.get("business_goal") or "")
    in_scope = intake.get("in_scope") or []
    if not isinstance(in_scope, list):
        in_scope = []
    criteria = intake.get("testable_acceptance_criteria") or []
    if not isinstance(criteria, list):
        criteria = []

    scenario_set: list[dict[str, Any]] = [
        {
            "title": f"Primary success path for {story_key}",
            "type": "positive",
            "preconditions": ["User has required access", "System in nominal configuration"],
            "steps_outline": [
                "Arrange data or state implied by story scope",
                "Execute main user action described in the business goal",
                "Observe system response and persisted state",
            ],
            "expected_results": [
                goal[:200] + ("…" if len(goal) > 200 else ""),
                "No unexpected errors in application logs for the path",
            ],
        },
        {
            "title": "Invalid input / failure handling",
            "type": "negative",
            "preconditions": ["Feature entry point available"],
            "steps_outline": [
                "Provide invalid or disallowed inputs per domain rules",
                "Submit or trigger the action",
            ],
            "expected_results": [
                "User receives a clear, safe error message",
                "System remains consistent (no partial corrupt state)",
            ],
        },
        {
            "title": "Boundary / edge conditions",
            "type": "edge",
            "preconditions": ["Representative edge data available"],
            "steps_outline": [
                "Exercise upper and lower bounds derived from scope bullets",
                "Repeat under minimum and maximum realistic volumes where applicable",
            ],
            "expected_results": [
                "Behavior remains defined; degrades gracefully if limits exceeded",
            ],
        },
    ]

    for i, c in enumerate(criteria[:2]):
        text = c.get("text") if isinstance(c, dict) else str(c)
        if not text:
            continue
        scenario_set.append(
            {
                "title": f"Acceptance-linked check {i + 1}",
                "type": "positive",
                "preconditions": ["Mapped acceptance criterion available"],
                "steps_outline": [
                    "Set up state so the criterion is observable",
                    "Perform the minimal steps to assert the criterion",
                ],
                "expected_results": [str(text)[:500]],
            }
        )

    return {
        "story_key": story_key,
        "scenario_set": scenario_set,
        "data_needs": [
            "Representative accounts / roles for the flows above",
            "Sample payloads matching in-scope bullets: "
            + "; ".join(str(x) for x in in_scope[:3])
            if in_scope
            else "Domain-specific fixtures TBD",
        ],
        "environment_needs": [
            "Staging or equivalent with feature flags aligned to the story",
            "Observability access (logs) for failure scenarios",
        ],
        "coverage_notes": [
            "Depth increases after human approval of this draft",
            "Automation candidates to be tagged post-review",
        ],
        "assumptions": list(intake.get("assumptions") or [])[:5]
        + ["Draft generated without live system exploration"],
    }
