"""Deterministic refine/regenerate from plain-text feedback (no LLM)."""

from __future__ import annotations

import copy
from typing import Any

from app.agents.test_design_agent import run_test_design


def refine_test_design_content(content: dict[str, Any], feedback_text: str) -> dict[str, Any]:
    """Apply conservative keyword-driven edits to an existing test design JSON."""
    out = copy.deepcopy(content) if isinstance(content, dict) else {}
    scenarios = list(out.get("scenario_set") or [])
    if not isinstance(scenarios, list):
        scenarios = []
    fb = (feedback_text or "").lower()

    if "negative" in fb:
        has_neg = any(str(s.get("type") or "").lower() == "negative" for s in scenarios if isinstance(s, dict))
        if not has_neg:
            scenarios.append(
                {
                    "title": "Feedback-driven negative scenario",
                    "type": "negative",
                    "preconditions": ["Feature reachable for invalid input path"],
                    "steps_outline": [
                        "Construct invalid or disallowed input per reviewer feedback",
                        "Execute the action and capture user-visible outcome",
                    ],
                    "expected_results": [
                        "Clear, safe failure path; no data corruption",
                    ],
                }
            )

    if "stepwise" in fb or "detailed" in fb:
        for s in scenarios:
            if not isinstance(s, dict):
                continue
            steps = list(s.get("steps_outline") or [])
            s["steps_outline"] = [
                f"{st} — add substeps: arrange, act, assert per reviewer request." for st in steps if st
            ] or [
                "Step 1: Arrange detailed fixtures.",
                "Step 2: Execute with traceable inputs.",
                "Step 3: Assert outcomes and side effects.",
            ]

    if "minimal" in fb:
        scenarios = scenarios[:2]

    if "positive only" in fb:
        scenarios = [s for s in scenarios if isinstance(s, dict) and str(s.get("type") or "").lower() == "positive"][
            :3
        ]

    if "positive and negative" in fb:
        pos_list = [
            s for s in scenarios if isinstance(s, dict) and str(s.get("type") or "").lower() == "positive"
        ]
        neg_list = [
            s for s in scenarios if isinstance(s, dict) and str(s.get("type") or "").lower() == "negative"
        ]
        picked: list[dict[str, Any]] = []
        if pos_list:
            picked.append(pos_list[0])
        if neg_list:
            picked.append(neg_list[0])
        if picked:
            scenarios = picked

    out["scenario_set"] = scenarios
    trace = list(out.get("feedback_trace") or [])
    trace.append((feedback_text or "")[:800])
    out["feedback_trace"] = trace[-10:]
    return out


def regenerate_test_design_content(
    intake: dict[str, Any],
    feedback_text: str,
) -> dict[str, Any]:
    """Rebuild test design from story intake using ``run_test_design``, then bias from feedback."""
    intake_copy = copy.deepcopy(intake) if isinstance(intake, dict) else {}
    fb = (feedback_text or "").lower()
    if "regenerate" in fb or "detailed" in fb or "stepwise" in fb:
        intake_copy["business_goal"] = str(intake_copy.get("business_goal") or "") + (
            " [regenerate: prefer detailed, stepwise scenarios per reviewer]"
        )
    if "minimal" in fb or "positive only" in fb:
        intake_copy["business_goal"] = str(intake_copy.get("business_goal") or "") + (
            " [regenerate: prefer minimal positive-only set per reviewer]"
        )
    base = run_test_design(intake_copy)
    if "minimal" in fb or "positive only" in fb:
        scenarios = [s for s in base.get("scenario_set") or [] if isinstance(s, dict) and s.get("type") == "positive"][
            :2
        ]
        base["scenario_set"] = scenarios
    if "positive and negative" in fb:
        ss = [s for s in base.get("scenario_set") or [] if isinstance(s, dict)]
        pos = next((s for s in ss if str(s.get("type") or "").lower() == "positive"), None)
        neg = next((s for s in ss if str(s.get("type") or "").lower() == "negative"), None)
        picked: list[dict[str, Any]] = []
        if pos:
            picked.append(pos)
        if neg:
            picked.append(neg)
        if picked:
            base["scenario_set"] = picked
    trace = list(base.get("feedback_trace") or [])
    trace.append((feedback_text or "")[:800])
    base["feedback_trace"] = trace[-10:]
    return base
