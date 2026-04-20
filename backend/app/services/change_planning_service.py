"""Validate provider-produced change plans and run planning orchestration."""

from __future__ import annotations

from typing import Any

from app.db.models.automation_job import AutomationJob
from app.providers.coding.base import CodeIntelligenceProvider
from app.providers.coding.registry import get_coding_provider
from app.services.planning_prompt_service import build_planning_payload

ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "tests/",
    "e2e/",
    "playwright/",
    "__tests__/",
    "pages/",
    "page-objects/",
    "pom/",
    "poms/",
    "utils/",
    "helpers/",
    "lib/",
)

MAX_PER_LIST = 15
MAX_TOTAL_PATHS = 40
VALID_ACTIONS = frozenset({"create", "modify"})


class PlanningValidationError(Exception):
    """Raised when a provider plan fails safety / shape checks."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _path_allowed(path: str) -> bool:
    if not isinstance(path, str) or not path.strip():
        return False
    p = path.strip().replace("\\", "/")
    if ".." in p or p.startswith("/"):
        return False
    return any(p.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES)


def _avoid_path_ok(path: str) -> bool:
    """Allow repo-relative paths or root-level Playwright config names only."""
    if _path_allowed(path):
        return True
    p = path.strip().replace("\\", "/")
    if "/" in p or ".." in p:
        return False
    low = p.lower()
    return low.startswith("playwright.config")


def _as_str_list(val: Any, *, field: str) -> tuple[list[str] | None, str | None]:
    if not isinstance(val, list):
        return None, f"{field} must be a list"
    out: list[str] = []
    for x in val:
        if not isinstance(x, str):
            return None, f"{field} entries must be strings"
        t = x.strip()
        if t:
            out.append(t.replace("\\", "/"))
    if len(out) > MAX_PER_LIST:
        return None, f"{field} exceeds max length {MAX_PER_LIST}"
    return out, None


def validate_change_plan(plan: dict[str, Any], job: AutomationJob) -> tuple[bool, str]:
    """Return (True, '') or (False, reason)."""
    if not isinstance(plan, dict):
        return False, "plan must be an object"

    required = (
        "framework_type",
        "target_test_file",
        "action_on_target_test_file",
        "files_to_create",
        "files_to_modify",
        "files_to_reuse",
        "planning_rationale",
    )
    for k in required:
        if k not in plan:
            return False, f"missing required key: {k}"

    action = plan.get("action_on_target_test_file")
    if action not in VALID_ACTIONS:
        return False, "action_on_target_test_file must be 'create' or 'modify'"

    fw = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else {}
    expected_ft = fw.get("framework_type")
    got_ft = plan.get("framework_type")
    if expected_ft and got_ft != expected_ft:
        return False, "framework_type does not match framework summary"

    target = plan.get("target_test_file")
    if not isinstance(target, str) or not target.strip():
        return False, "target_test_file must be a non-empty string"
    if not _path_allowed(target):
        return False, f"target_test_file not in allowed repo areas: {target}"

    for key in ("files_to_create", "files_to_modify", "files_to_reuse"):
        lst, err = _as_str_list(plan.get(key), field=key)
        if err:
            return False, err
        assert lst is not None
        for p in lst:
            if not _path_allowed(p):
                return False, f"invalid path in {key}: {p}"

    rat, err = _as_str_list(plan.get("planning_rationale"), field="planning_rationale")
    if err:
        return False, err
    if not rat:
        return False, "planning_rationale must be non-empty"

    all_paths: set[str] = {target.strip().replace("\\", "/")}
    for key in ("files_to_create", "files_to_modify", "files_to_reuse"):
        lst, _ = _as_str_list(plan.get(key), field=key)
        assert lst is not None
        all_paths.update(lst)

    if len(all_paths) > MAX_TOTAL_PATHS:
        return False, f"too many unique paths ({len(all_paths)} > {MAX_TOTAL_PATHS})"

    avoid = plan.get("files_to_avoid")
    if avoid is not None:
        if not isinstance(avoid, list):
            return False, "files_to_avoid must be a list"
        for p in avoid:
            if not isinstance(p, str) or not _avoid_path_ok(p):
                return False, f"invalid files_to_avoid entry: {p}"
        if len(avoid) > MAX_PER_LIST:
            return False, "files_to_avoid too long"

    for key in ("scope_notes", "risk_notes"):
        if key in plan and plan[key] is not None:
            if not isinstance(plan[key], list):
                return False, f"{key} must be a list when present"
            if len(plan[key]) > MAX_PER_LIST:
                return False, f"{key} too long"

    return True, ""


def normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with optional keys defaulted."""
    out = dict(plan)
    out.setdefault("files_to_avoid", [])
    out.setdefault("scope_notes", [])
    out.setdefault("risk_notes", [])
    for k in ("files_to_avoid", "scope_notes", "risk_notes"):
        if not isinstance(out[k], list):
            out[k] = []
        out[k] = [str(x).strip() for x in out[k] if isinstance(x, str) and str(x).strip()][
            :MAX_PER_LIST
        ]
    return out


def create_validated_change_plan(
    job: AutomationJob,
    *,
    provider: CodeIntelligenceProvider | None = None,
) -> dict[str, Any]:
    """
    Build payload, invoke provider, validate, normalize.

    Raises:
        PlanningValidationError: if the plan fails validation.
    """
    payload = build_planning_payload(job)
    p = provider or get_coding_provider()
    raw = p.create_change_plan(payload)
    ok, msg = validate_change_plan(raw, job)
    if not ok:
        raise PlanningValidationError(msg)
    return normalize_plan(raw)
