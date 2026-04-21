"""Build canonical ``TestDesignPublishPackage`` from stored test design artifacts."""

from __future__ import annotations

import uuid
from typing import Any, cast

from app.schemas.test_design_publish import CaseType, TestCaseDraft, TestDesignPublishPackage

_VALID_CASE_TYPES = frozenset({"positive", "negative", "validation", "edge", "generic"})


def _norm_case_type(raw: str | None) -> str:
    t = (raw or "generic").strip().lower()
    return t if t in _VALID_CASE_TYPES else "generic"


def draft_cases_from_test_design_json(content: dict[str, Any], *, max_cases: int = 3) -> list[TestCaseDraft]:
    """Map ``test_design_agent`` JSON to up to ``max_cases`` canonical drafts."""
    scenarios = content.get("scenario_set") or []
    if not isinstance(scenarios, list):
        return []
    global_assumptions = list(content.get("assumptions") or [])[:10]
    if not isinstance(global_assumptions, list):
        global_assumptions = []
    data_needs = content.get("data_needs") or []
    if not isinstance(data_needs, list):
        data_needs = []
    missing = [str(x) for x in data_needs[:5] if str(x).strip()]

    out: list[TestCaseDraft] = []
    for raw in scenarios[:max_cases]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        pre = raw.get("preconditions") or []
        if not isinstance(pre, list):
            pre = []
        steps = raw.get("steps_outline") or raw.get("steps") or []
        if not isinstance(steps, list):
            steps = []
        exp = raw.get("expected_results") or []
        if not isinstance(exp, list):
            exp = []
        ctype = cast(CaseType, _norm_case_type(raw.get("type")))
        objective = title if len(title) > 10 else f"Validate: {title}"
        local_assumptions = list(global_assumptions)
        out.append(
            TestCaseDraft(
                title=title,
                case_type=ctype,
                objective=objective,
                preconditions=[str(x) for x in pre if str(x).strip()],
                steps=[str(x) for x in steps if str(x).strip()],
                expected_results=[str(x) for x in exp if str(x).strip()],
                assumptions=local_assumptions,
                missing_information=missing,
            )
        )
    return out


def build_publish_package(
    *,
    parent_issue_key: str,
    workflow_run_id: uuid.UUID,
    source_artifact_id: uuid.UUID,
    test_design_content_json: dict[str, Any] | None,
) -> TestDesignPublishPackage:
    cases = draft_cases_from_test_design_json(test_design_content_json or {})
    return TestDesignPublishPackage(
        parent_issue_key=parent_issue_key.strip().upper(),
        workflow_run_id=workflow_run_id,
        source_artifact_id=source_artifact_id,
        cases=cases,
    )
