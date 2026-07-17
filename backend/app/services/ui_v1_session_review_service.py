"""Review-oriented session payload for ``GET /api/v1/sessions/{id}/review-data``."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.automation_patch_version import AutomationPatchVersion
from app.services import automation_pr_service, automation_session_service
from app.services.git_workspace_service import (
    GitWorkspaceError,
    ensure_git_repo,
    read_file_at_git_ref,
    resolve_base_branch_ref,
)
from app.services.ui_v1_dashboard import map_backend_to_ui_dashboard_status
from app.services.ui_v1_mapper import dict_keys_to_camel

_MAX_FILE_CONTENT_CHARS = 256_000


def _s(val: Any, *, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    return str(val)


def _content_hash(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_generated_files(patch_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(patch_json, dict):
        return []
    gf = patch_json.get("generated_files")
    if isinstance(gf, tuple):
        gf = list(gf)
    if not isinstance(gf, list) or not gf:
        return []
    out: list[dict[str, Any]] = []
    for x in gf:
        if not isinstance(x, dict):
            continue
        if not isinstance(x.get("path"), str) or not isinstance(x.get("action"), str):
            continue
        raw = x.get("content")
        if isinstance(raw, bytes):
            content = raw.decode("utf-8", errors="replace")
        elif isinstance(raw, str):
            content = raw
        else:
            continue
        out.append({"path": x["path"], "action": x["action"], "content": content})
    return out


def _files_by_path(patch_json: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {str(f["path"]): f for f in _extract_generated_files(patch_json)}


def _maybe_truncate_content(text: str | None) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    if len(text) <= _MAX_FILE_CONTENT_CHARS:
        return text, False
    return text[:_MAX_FILE_CONTENT_CHARS], True


def _read_base_branch_content(repo_path: str | None, base_branch: str, rel_path: str) -> str | None:
    if not repo_path or not rel_path:
        return None
    try:
        repo = ensure_git_repo(Path(repo_path))
        base_ref = resolve_base_branch_ref(repo, (base_branch or "main").strip() or "main")
        return read_file_at_git_ref(repo, base_ref, rel_path)
    except (GitWorkspaceError, OSError, ValueError):
        return None


def _round_number_by_id(rounds: list[dict[str, Any]]) -> dict[str, int]:
    return {str(r["id"]): int(r.get("round_number") or 0) for r in rounds if r.get("id")}


def _derive_next_actions(summary: dict[str, Any]) -> list[str]:
    ui_status = map_backend_to_ui_dashboard_status(summary)
    workflow = _s(summary.get("status"))
    if ui_status == "draft":
        return ["start_automation"]
    if ui_status in ("running", "revising"):
        return []
    if ui_status == "awaiting_review":
        return ["request_revision", "approve"]
    if workflow == "approved_for_pr" or ui_status == "queued":
        return ["create_pr"]
    if workflow == "creating_pr":
        return []
    if workflow == "pr_created" or ui_status == "succeeded":
        return ["open_pr", "view_summary"]
    if ui_status == "failed" or workflow in ("failed", "pr_failed", "pr_creation_failed"):
        return ["view_details"]
    return []


def _map_timeline_status(raw: Any, *, action_type: str | None = None) -> str:
    s = str(raw or "").lower().strip()
    action = str(action_type or "").lower().strip()
    if s in ("addressed", "dismissed", "open", "passed", "failed", "created", "recorded"):
        return s
    if s in ("applied", "recorded") and action == "approve":
        return "addressed"
    if s in ("closed", "resolved", "applied"):
        return "addressed"
    return "open"


def _build_review_timeline(
    *,
    rounds: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    executions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    round_by_id = _round_number_by_id(rounds)
    items: list[dict[str, Any]] = []

    for r in reviews:
        action = _s(r.get("action_type"))
        rid = r.get("revision_round_id")
        round_n = round_by_id.get(str(rid), 0) if rid else 0
        text = _s(r.get("instruction_text"))
        if not text and action == "approve":
            text = "Approved for publish."
        elif not text and action == "manual_edit_ack":
            text = "Manual edit acknowledged."
        item: dict[str, Any] = {
            "id": _s(r.get("id")),
            "type": action or "review",
            "actor": _s(r.get("actor_id"), default="system"),
            "text": text[:8000],
            "created_at": r.get("created_at") or "",
            "round_number": round_n,
            "status": _map_timeline_status(r.get("status"), action_type=action or None),
        }
        scope = r.get("target_scope")
        if scope:
            item["scope"] = str(scope)[:8000]
        items.append(item)

    for e in executions:
        success = e.get("success")
        st = "passed" if success is True else "failed" if success is False else "pending"
        rj = e.get("result_json") if isinstance(e.get("result_json"), dict) else {}
        summary = ""
        for key in ("summary", "message", "stdout_tail"):
            v = rj.get(key)
            if v:
                summary = str(v)[:4000]
                break
        if not summary:
            summary = "Validation run passed." if success else "Validation run failed."
        rid = e.get("revision_round_id")
        round_n = round_by_id.get(str(rid), int(e.get("attempt_number") or 0)) if rid else int(
            e.get("attempt_number") or 0
        )
        items.append(
            {
                "id": _s(e.get("id")),
                "type": "execution_result",
                "actor": "system",
                "text": summary,
                "created_at": e.get("created_at") or "",
                "round_number": round_n,
                "status": st,
            }
        )

    for rnd in rounds:
        if _s(rnd.get("status")) not in ("completed", "failed", "in_progress"):
            continue
        trigger = _s(rnd.get("trigger_type"))
        label = "Initial automation run completed." if trigger == "initial" else f"Round {rnd.get('round_number')} completed."
        if _s(rnd.get("status")) == "failed":
            label = label.replace("completed", "failed")
        items.append(
            {
                "id": f"round-{rnd.get('id')}",
                "type": "system",
                "actor": _s(rnd.get("started_by"), default="system"),
                "text": label,
                "created_at": rnd.get("created_at") or "",
                "round_number": int(rnd.get("round_number") or 0),
                "status": _map_timeline_status(rnd.get("status")),
            }
        )

    items.sort(key=lambda x: str(x.get("created_at") or ""))
    return items


def _build_pr_info(pr_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pr_items:
        return None
    last = pr_items[-1]
    if not isinstance(last, dict):
        return None
    out: dict[str, Any] = {
        "status": _s(last.get("status")),
        "title": _s(last.get("title"))[:512],
        "source_branch": _s(last.get("source_branch")),
        "target_branch": _s(last.get("target_branch")),
        "provider": _s(last.get("provider")),
        "code_review_request_id": _s(last.get("id")),
    }
    body = last.get("body")
    if body:
        out["body"] = str(body)[:8000]
    if last.get("external_url"):
        out["external_url"] = str(last["external_url"])[:1024]
    if last.get("external_id"):
        out["external_id"] = str(last["external_id"])[:256]
    return out


def _build_changed_files(
    *,
    current_patch: dict[str, Any],
    previous_patch: dict[str, Any] | None,
    repo_path: str | None,
    base_branch: str,
) -> list[dict[str, Any]]:
    current_files = _extract_generated_files(current_patch.get("patch_json"))
    previous_map = _files_by_path(previous_patch.get("patch_json") if previous_patch else None)
    prev_version = int(previous_patch.get("version_number") or 0) if previous_patch else 0
    cur_version = int(current_patch.get("version_number") or 0)
    prev_label = f"Code revision {prev_version}" if prev_version else f"Base branch ({base_branch or 'main'})"
    after_label = f"Code revision {cur_version}" if cur_version else "Current"

    changed: list[dict[str, Any]] = []
    for f in current_files:
        path = str(f["path"])
        action = str(f.get("action") or "modify")
        current_raw = f.get("content")
        current_content, cur_trunc = _maybe_truncate_content(
            current_raw if isinstance(current_raw, str) else None
        )

        previous_raw: str | None = None
        before_label = prev_label
        prev_entry = previous_map.get(path)
        if prev_entry is not None:
            previous_raw = prev_entry.get("content") if isinstance(prev_entry.get("content"), str) else None
            before_label = f"Code revision {prev_version}"
        elif prev_version == 0:
            previous_raw = _read_base_branch_content(repo_path, base_branch, path)
            before_label = f"Base branch ({base_branch or 'main'})"

        previous_content, prev_trunc = _maybe_truncate_content(previous_raw)
        content_changed = previous_content != current_content
        has_diff = content_changed and previous_content is not None and current_content is not None

        entry: dict[str, Any] = {
            "path": path,
            "action": action,
            "current_content": current_content,
            "previous_content": previous_content,
            "before_label": before_label,
            "after_label": after_label,
            "is_current": True,
            "has_diff": has_diff,
            "content_changed": content_changed,
            "current_content_hash": _content_hash(current_content),
            "previous_content_hash": _content_hash(previous_content),
            "current_byte_length": len(current_content.encode("utf-8")) if current_content else 0,
            "previous_byte_length": len(previous_content.encode("utf-8")) if previous_content else 0,
        }
        if cur_trunc:
            entry["current_content_truncated"] = True
        if prev_trunc:
            entry["previous_content_truncated"] = True
        changed.append(entry)
    return changed


def build_session_review_data_for_ui(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    """
    Review cockpit payload (snake_case internally; router returns camelCase).

    Raises:
        KeyError: session_not_found
    """
    sess = automation_session_service.get_session(db, session_id)
    if sess is None:
        raise KeyError("session_not_found")

    summary = automation_session_service.session_to_summary(db, sess)
    patches = automation_session_service.list_patch_versions_for_api(db, session_id)
    rounds = automation_session_service.list_rounds_for_api(db, session_id)
    executions = automation_session_service.list_execution_attempts_for_api(db, session_id)
    reviews = automation_session_service.list_review_requests_for_api(db, session_id)
    pr_items = automation_pr_service.list_code_review_requests_for_api(db, session_id)

    current_patch: dict[str, Any] | None = None
    previous_patch: dict[str, Any] | None = None
    for p in patches:
        if p.get("is_current"):
            current_patch = p
    if current_patch is None and patches:
        current_patch = patches[-1]
    if len(patches) >= 2:
        if current_patch and patches[-1].get("id") == current_patch.get("id"):
            previous_patch = patches[-2]
        elif len(patches) >= 2:
            previous_patch = patches[-2]

    latest_execution_status = "pending"
    validation_summary = ""
    if executions:
        last_ex = executions[-1]
        if last_ex.get("success") is True:
            latest_execution_status = "passed"
        elif last_ex.get("success") is False:
            latest_execution_status = "failed"
        rj = last_ex.get("result_json") if isinstance(last_ex.get("result_json"), dict) else {}
        for key in ("summary", "message", "stdout_tail"):
            v = rj.get(key)
            if v:
                validation_summary = str(v)[:4000]
                break

    changed_files: list[dict[str, Any]] = []
    if current_patch is not None:
        repo_path = _s(summary.get("repo_path")) or None
        base_branch = _s(summary.get("base_branch"), default="main")
        changed_files = _build_changed_files(
            current_patch=current_patch,
            previous_patch=previous_patch,
            repo_path=repo_path,
            base_branch=base_branch,
        )

    review_state = map_backend_to_ui_dashboard_status(summary)
    workflow_status = _s(summary.get("status"))

    review_summary: dict[str, Any] = {
        "current_patch_version": int(current_patch.get("version_number") or 0) if current_patch else 0,
        "current_patch_version_id": _s(current_patch.get("id")) if current_patch else "",
        "latest_execution_status": latest_execution_status,
        "validation_summary": validation_summary,
        "changed_files_count": len(changed_files),
        "review_state": review_state,
        "workflow_status": workflow_status,
        "next_actions": _derive_next_actions(summary),
    }
    if current_patch and current_patch.get("created_at"):
        review_summary["current_patch_created_at"] = current_patch["created_at"]

    payload = {
        "session_id": str(session_id),
        "review_summary": review_summary,
        "changed_files": changed_files,
        "review_conversation": _build_review_timeline(rounds=rounds, reviews=reviews, executions=executions),
        "pr_info": _build_pr_info(pr_items if isinstance(pr_items, list) else []),
    }
    return dict_keys_to_camel(payload)
