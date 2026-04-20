"""Validate provider-generated patches against the approved change plan."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.db.models.automation_job import AutomationJob
from app.services.change_planning_service import ALLOWED_PATH_PREFIXES

MAX_FILES_IN_PATCH = 20
MAX_BYTES_PER_FILE = 256 * 1024
MAX_TOTAL_CONTENT_BYTES = 2 * 1024 * 1024
VALID_FILE_ACTIONS = frozenset({"create", "modify"})

PROSE_PATTERNS = (
    re.compile(r"^\s*here is the (updated )?file", re.I | re.M),
    re.compile(r"^\s*below is the", re.I | re.M),
    re.compile(r"^\s*I've updated", re.I | re.M),
)


class PatchValidationError(Exception):
    """Raised when a generated patch fails shape, scope, or safety checks."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _norm_rel(p: str) -> str:
    return p.strip().replace("\\", "/")


def _path_shape_ok(path: str) -> bool:
    p = _norm_rel(path)
    if not p or ".." in p or p.startswith("/"):
        return False
    if not any(p.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
        return False
    return True


def _content_sanity(content: str) -> tuple[bool, str]:
    if not isinstance(content, str):
        return False, "content must be a string"
    if not content.strip():
        return False, "content must be non-empty"
    if len(content.encode("utf-8")) > MAX_BYTES_PER_FILE:
        return False, "content exceeds max bytes per file"
    if "```" in content:
        return False, "content must not contain markdown code fences"
    for pat in PROSE_PATTERNS:
        if pat.search(content):
            return False, "content must not contain assistant-style prose"
    return True, ""


def validate_generated_patch(patch: dict[str, Any], job: AutomationJob) -> None:
    """
    Validate ``patch`` against ``job.change_plan_json`` and framework summary.

    Raises:
        PatchValidationError: on any violation (caller must not write files).
    """
    if not isinstance(patch, dict):
        raise PatchValidationError("patch must be an object")

    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else None
    if plan is None:
        raise PatchValidationError("job has no change_plan_json")

    required_top = ("framework_type", "target_test_file", "generated_files")
    for k in required_top:
        if k not in patch:
            raise PatchValidationError(f"missing required key: {k}")

    fw_job = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else {}
    expected_fw = fw_job.get("framework_type")
    got_fw = patch.get("framework_type")
    if expected_fw and got_fw != expected_fw:
        raise PatchValidationError("framework_type does not match framework summary")

    plan_target = _norm_rel(str(plan.get("target_test_file") or ""))
    patch_target = _norm_rel(str(patch.get("target_test_file") or ""))
    if not plan_target:
        raise PatchValidationError("plan has no target_test_file")
    if patch_target != plan_target:
        raise PatchValidationError("target_test_file does not match change plan")

    plan_action = plan.get("action_on_target_test_file")
    if plan_action not in ("create", "modify"):
        raise PatchValidationError("plan has invalid action_on_target_test_file")

    files_create = {_norm_rel(x) for x in (plan.get("files_to_create") or []) if isinstance(x, str)}
    files_modify = {_norm_rel(x) for x in (plan.get("files_to_modify") or []) if isinstance(x, str)}
    files_avoid = {_norm_rel(x) for x in (plan.get("files_to_avoid") or []) if isinstance(x, str)}
    files_reuse = {_norm_rel(x) for x in (plan.get("files_to_reuse") or []) if isinstance(x, str)}

    allowed_output = set(files_create) | set(files_modify)
    if not allowed_output:
        raise PatchValidationError("plan has no files_to_create or files_to_modify")

    gf = patch.get("generated_files")
    if not isinstance(gf, list) or not gf:
        raise PatchValidationError("generated_files must be a non-empty list")
    if len(gf) > MAX_FILES_IN_PATCH:
        raise PatchValidationError("too many generated_files")

    seen_paths: set[str] = set()
    total_bytes = 0

    for i, item in enumerate(gf):
        if not isinstance(item, dict):
            raise PatchValidationError(f"generated_files[{i}] must be an object")
        for key in ("path", "action", "content"):
            if key not in item:
                raise PatchValidationError(f"generated_files[{i}] missing {key}")
        path = _norm_rel(str(item["path"]))
        action = item["action"]
        content = item["content"]

        if path in seen_paths:
            raise PatchValidationError(f"duplicate path in generated_files: {path}")
        seen_paths.add(path)

        if action not in VALID_FILE_ACTIONS:
            raise PatchValidationError(f"invalid action for {path}")
        if not _path_shape_ok(path):
            raise PatchValidationError(f"invalid or disallowed path: {path}")
        if path not in allowed_output:
            raise PatchValidationError(f"path not in plan create/modify scope: {path}")

        if path in files_avoid:
            raise PatchValidationError(f"path is in files_to_avoid: {path}")
        if path in files_reuse:
            raise PatchValidationError(f"path is files_to_reuse and must not be written: {path}")

        if action == "create" and path not in files_create:
            raise PatchValidationError(f"action create not allowed for path per plan: {path}")
        if action == "modify" and path not in files_modify:
            raise PatchValidationError(f"action modify not allowed for path per plan: {path}")

        ok_c, msg_c = _content_sanity(content)
        if not ok_c:
            raise PatchValidationError(f"{path}: {msg_c}")
        total_bytes += len(str(content).encode("utf-8"))

        if path == patch_target and action != plan_action:
            raise PatchValidationError(
                f"target_test_file action {action} does not match plan action {plan_action}"
            )

    if patch_target not in seen_paths:
        raise PatchValidationError("target_test_file must appear in generated_files")

    planned_all = set(files_create) | set(files_modify)
    if seen_paths != planned_all:
        raise PatchValidationError(
            "generated_files must cover exactly all planned create/modify paths"
        )

    if total_bytes > MAX_TOTAL_CONTENT_BYTES:
        raise PatchValidationError("total generated content exceeds cap")

    reused = patch.get("reused_files")
    if reused is not None:
        if not isinstance(reused, list):
            raise PatchValidationError("reused_files must be a list when present")
        if len(reused) > 30:
            raise PatchValidationError("reused_files too long")
        for p in reused:
            if not isinstance(p, str) or not _norm_rel(p):
                raise PatchValidationError("reused_files entries must be non-empty strings")
            n = _norm_rel(p)
            if n in seen_paths:
                raise PatchValidationError("reused_files path must not overlap generated_files")

    notes = patch.get("generation_notes")
    if notes is not None:
        if not isinstance(notes, list):
            raise PatchValidationError("generation_notes must be a list when present")
        if len(notes) > 30:
            raise PatchValidationError("generation_notes too long")
        for n in notes:
            if not isinstance(n, str):
                raise PatchValidationError("generation_notes entries must be strings")


def validate_repair_patch(patch: dict[str, Any], job: AutomationJob) -> None:
    """
    Validate a repair patch: same safety as generation, but ``generated_files`` may be a
    **non-empty subset** of planned ``files_to_create`` ∪ ``files_to_modify`` (not required
    to touch every planned path).
    """
    if not isinstance(patch, dict):
        raise PatchValidationError("patch must be an object")

    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else None
    if plan is None:
        raise PatchValidationError("job has no change_plan_json")

    required_top = ("framework_type", "target_test_file", "generated_files")
    for k in required_top:
        if k not in patch:
            raise PatchValidationError(f"missing required key: {k}")

    fw_job = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else {}
    expected_fw = fw_job.get("framework_type")
    got_fw = patch.get("framework_type")
    if expected_fw and got_fw != expected_fw:
        raise PatchValidationError("framework_type does not match framework summary")

    plan_target = _norm_rel(str(plan.get("target_test_file") or ""))
    patch_target = _norm_rel(str(patch.get("target_test_file") or ""))
    if not plan_target:
        raise PatchValidationError("plan has no target_test_file")
    if patch_target != plan_target:
        raise PatchValidationError("target_test_file does not match change plan")

    plan_action = plan.get("action_on_target_test_file")
    if plan_action not in ("create", "modify"):
        raise PatchValidationError("plan has invalid action_on_target_test_file")

    files_create = {_norm_rel(x) for x in (plan.get("files_to_create") or []) if isinstance(x, str)}
    files_modify = {_norm_rel(x) for x in (plan.get("files_to_modify") or []) if isinstance(x, str)}
    files_avoid = {_norm_rel(x) for x in (plan.get("files_to_avoid") or []) if isinstance(x, str)}
    files_reuse = {_norm_rel(x) for x in (plan.get("files_to_reuse") or []) if isinstance(x, str)}

    allowed_output = set(files_create) | set(files_modify)
    if not allowed_output:
        raise PatchValidationError("plan has no files_to_create or files_to_modify")

    gf = patch.get("generated_files")
    if not isinstance(gf, list) or not gf:
        raise PatchValidationError("generated_files must be a non-empty list")
    if len(gf) > MAX_FILES_IN_PATCH:
        raise PatchValidationError("too many generated_files")

    seen_paths: set[str] = set()
    total_bytes = 0

    for i, item in enumerate(gf):
        if not isinstance(item, dict):
            raise PatchValidationError(f"generated_files[{i}] must be an object")
        for key in ("path", "action", "content"):
            if key not in item:
                raise PatchValidationError(f"generated_files[{i}] missing {key}")
        path = _norm_rel(str(item["path"]))
        action = item["action"]
        content = item["content"]

        if path in seen_paths:
            raise PatchValidationError(f"duplicate path in generated_files: {path}")
        seen_paths.add(path)

        if action not in VALID_FILE_ACTIONS:
            raise PatchValidationError(f"invalid action for {path}")
        if not _path_shape_ok(path):
            raise PatchValidationError(f"invalid or disallowed path: {path}")
        if path not in allowed_output:
            raise PatchValidationError(f"path not in plan create/modify scope: {path}")

        if path in files_avoid:
            raise PatchValidationError(f"path is in files_to_avoid: {path}")
        if path in files_reuse:
            raise PatchValidationError(f"path is files_to_reuse and must not be written: {path}")

        if action == "create" and path not in files_create:
            raise PatchValidationError(f"action create not allowed for path per plan: {path}")
        if action == "modify" and path not in files_modify:
            raise PatchValidationError(f"action modify not allowed for path per plan: {path}")

        ok_c, msg_c = _content_sanity(content)
        if not ok_c:
            raise PatchValidationError(f"{path}: {msg_c}")
        total_bytes += len(str(content).encode("utf-8"))

        if path == patch_target and action != plan_action:
            raise PatchValidationError(
                f"target_test_file action {action} does not match plan action {plan_action}"
            )

    if patch_target not in seen_paths:
        raise PatchValidationError("target_test_file must appear in generated_files")

    if not seen_paths.issubset(allowed_output):
        raise PatchValidationError("generated_files paths must stay within plan scope")

    if total_bytes > MAX_TOTAL_CONTENT_BYTES:
        raise PatchValidationError("total generated content exceeds cap")

    reused = patch.get("reused_files")
    if reused is not None:
        if not isinstance(reused, list):
            raise PatchValidationError("reused_files must be a list when present")
        if len(reused) > 30:
            raise PatchValidationError("reused_files too long")
        for p in reused:
            if not isinstance(p, str) or not _norm_rel(p):
                raise PatchValidationError("reused_files entries must be non-empty strings")
            n = _norm_rel(p)
            if n in seen_paths:
                raise PatchValidationError("reused_files path must not overlap generated_files")

    notes = patch.get("generation_notes")
    if notes is not None:
        if not isinstance(notes, list):
            raise PatchValidationError("generation_notes must be a list when present")
        if len(notes) > 30:
            raise PatchValidationError("generation_notes too long")
        for n in notes:
            if not isinstance(n, str):
                raise PatchValidationError("generation_notes entries must be strings")


def summarize_patch_for_persistence(patch: dict[str, Any]) -> dict[str, Any]:
    """Strip heavy content; keep metadata for ``generated_patch_json``."""
    files_out: list[dict[str, Any]] = []
    for item in patch.get("generated_files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip().replace("\\", "/")
        content = item.get("content", "")
        raw = content if isinstance(content, str) else ""
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""
        files_out.append(
            {
                "path": path,
                "action": item.get("action"),
                "byte_length": len(raw.encode("utf-8")),
                "content_sha256": h,
            }
        )
    return {
        "framework_type": patch.get("framework_type"),
        "target_test_file": patch.get("target_test_file"),
        "generated_files": files_out,
        "reused_files": list(patch.get("reused_files") or []),
        "generation_notes": list(patch.get("generation_notes") or []),
    }
