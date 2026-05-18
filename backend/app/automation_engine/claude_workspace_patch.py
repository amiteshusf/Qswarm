"""Build raw patch dicts from on-disk workspace + change plan (external CLI engines)."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.db.models.automation_job import AutomationJob
from app.services.patch_validation_service import _norm_rel


def _read_text(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(encoding="utf-8")


def plan_paths_in_order(job: AutomationJob) -> list[str]:
    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else {}
    files_modify = [_norm_rel(x) for x in (plan.get("files_to_modify") or []) if isinstance(x, str)]
    files_create = [_norm_rel(x) for x in (plan.get("files_to_create") or []) if isinstance(x, str)]
    out: list[str] = []
    for p in files_modify:
        if p:
            out.append(p)
    for p in files_create:
        if p:
            out.append(p)
    return out


def paths_for_revision_scope(job: AutomationJob, target_scope: str | None) -> list[str]:
    """Paths to include in a repair patch: explicit scope tokens intersected with plan, else full plan order."""
    ordered = plan_paths_in_order(job)
    if not ordered:
        raise ValueError("plan_has_no_paths")
    ts = (target_scope or "").strip()
    if not ts:
        return ordered
    allowed = set(ordered)
    chosen: list[str] = []
    for part in re.split(r"[\s,]+", ts):
        p = _norm_rel(part)
        if p and p in allowed and p not in chosen:
            chosen.append(p)
    return chosen if chosen else ordered


def build_full_generation_patch_from_workspace(
    job: AutomationJob,
    root: Path,
    *,
    engine_run_label: str = "claude_code",
) -> dict[str, Any]:
    """
    Build a ``generated_files`` list covering **exactly** all plan create/modify paths.

    Raises ``FileNotFoundError`` if a planned path is missing on disk.
    """
    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else {}
    if not plan:
        raise ValueError("missing_change_plan")

    files_create = [_norm_rel(x) for x in (plan.get("files_to_create") or []) if isinstance(x, str)]
    files_modify = [_norm_rel(x) for x in (plan.get("files_to_modify") or []) if isinstance(x, str)]
    ordered: list[tuple[str, str]] = []
    for p in files_modify:
        if p:
            ordered.append((p, "modify"))
    for p in files_create:
        if p:
            ordered.append((p, "create"))

    if not ordered:
        raise ValueError("plan_has_no_paths")

    fw = plan.get("framework_type") or (
        (job.framework_summary_json or {}).get("framework_type") if isinstance(job.framework_summary_json, dict) else "playwright"
    )
    target = _norm_rel(str(plan.get("target_test_file") or ""))

    generated_files: list[dict[str, Any]] = []
    for path, action in ordered:
        if not (root / path).is_file():
            raise FileNotFoundError(path)
        generated_files.append({"path": path, "action": action, "content": _read_text(root, path)})

    return {
        "framework_type": str(fw),
        "target_test_file": target,
        "generated_files": generated_files,
        "reused_files": [],
        "generation_notes": [f"assembled_from_workspace_after_{engine_run_label}"],
    }


def build_repair_subset_patch_from_workspace(
    job: AutomationJob,
    root: Path,
    *,
    touched_paths: list[str],
    engine_run_label: str = "claude_code",
) -> dict[str, Any]:
    """Build a repair-style patch (non-empty subset of plan paths) from disk."""
    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else {}
    if not plan:
        raise ValueError("missing_change_plan")

    files_create = {_norm_rel(x) for x in (plan.get("files_to_create") or []) if isinstance(x, str)}
    files_modify = {_norm_rel(x) for x in (plan.get("files_to_modify") or []) if isinstance(x, str)}
    allowed = files_create | files_modify

    fw = plan.get("framework_type") or (
        (job.framework_summary_json or {}).get("framework_type") if isinstance(job.framework_summary_json, dict) else "playwright"
    )
    target = _norm_rel(str(plan.get("target_test_file") or ""))

    generated_files: list[dict[str, Any]] = []
    for raw in touched_paths:
        path = _norm_rel(str(raw))
        if not path or path not in allowed:
            continue
        if not (root / path).is_file():
            raise FileNotFoundError(path)
        action = "create" if path in files_create else "modify"
        generated_files.append({"path": path, "action": action, "content": _read_text(root, path)})

    if not generated_files:
        raise ValueError("no_touched_paths_resolved")

    if target not in {g["path"] for g in generated_files}:
        # ensure target file included if it exists
        if (root / target).is_file():
            ta = "create" if target in files_create else "modify"
            generated_files.insert(
                0, {"path": target, "action": ta, "content": _read_text(root, target)}
            )

    return {
        "framework_type": str(fw),
        "target_test_file": target,
        "generated_files": generated_files,
        "reused_files": [],
        "generation_notes": [f"assembled_from_workspace_after_{engine_run_label}_revision"],
    }
