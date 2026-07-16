"""
BFF normalization for ``/api/v1/sessions`` (Qswarm-UI session summary/detail schemas).

Maps internal ``session_to_summary`` and list APIs into ``sessionSummarySchema`` /
``sessionDetailSchema`` (see Qswarm-UI ``src/api/schemas.ts``). List responses are a
**top-level JSON array** (same pattern as repo connections and branch policies).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.services import automation_pr_service, automation_session_service
from app.services.ui_v1_branch_policies import branch_policy_id_for_connection
from app.services.ui_v1_dashboard import map_backend_to_ui_dashboard_status


def _s(val: Any, *, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    return str(val)


def format_session_summary_for_ui(summary: dict[str, Any]) -> dict[str, Any]:
    """One ``sessionSummarySchema`` row (camelCase)."""
    workflow_status = _s(summary.get("status"))
    out: dict[str, Any] = {
        "id": _s(summary.get("id")),
        "status": map_backend_to_ui_dashboard_status(summary),
        "workflowStatus": workflow_status,
        "engine": _s(summary.get("coding_engine"), default="stub"),
        "repoConnectionId": _s(summary.get("repository_connection_id")),
        "sourceRef": _s(summary.get("source_reference")),
        "createdAt": summary.get("created_at") or "",
        "updatedAt": summary.get("updated_at") or "",
    }
    label = _s(summary.get("approved_case_id"))
    if label:
        out["sourceLabel"] = label
    return out


def _map_round_ui_status(raw: Any) -> str:
    s = str(raw or "").lower().strip()
    if s in ("completed", "complete", "done", "success"):
        return "complete"
    if s in ("failed", "error"):
        return "failed"
    if s in ("in_progress", "running", "active"):
        return "active"
    return "planned"


def _format_round_for_ui(r: dict[str, Any]) -> dict[str, Any]:
    num = int(r.get("round_number") or 0)
    trigger = _s(r.get("trigger_type"))
    title = f"Round {num}" if num else "Round"
    if trigger and trigger not in ("initial", ""):
        title = f"{title} ({trigger})"
    out: dict[str, Any] = {
        "id": _s(r.get("id")),
        "number": num,
        "title": title[:512],
        "status": _map_round_ui_status(r.get("status")),
    }
    if r.get("created_at"):
        out["startedAt"] = r["created_at"]
    notes = r.get("instruction_text")
    if notes:
        out["notes"] = str(notes)[:8000]
    return out


def _format_patch_for_ui(p: dict[str, Any]) -> dict[str, Any]:
    pj = p.get("patch_json")
    out: dict[str, Any] = {
        "id": _s(p.get("id")),
        "version": int(p.get("version_number") or 0),
        "createdAt": p.get("created_at") or "",
    }
    if p.get("is_current"):
        out["label"] = "current"
    if isinstance(pj, dict):
        files = pj.get("files") or pj.get("changed_files")
        if isinstance(files, list):
            out["filesChanged"] = len(files)
        stats = pj.get("stats") if isinstance(pj.get("stats"), dict) else {}
        if isinstance(stats, dict):
            if "additions" in stats:
                out["additions"] = int(stats["additions"])
            if "deletions" in stats:
                out["deletions"] = int(stats["deletions"])
    return out


def _format_execution_for_ui(e: dict[str, Any]) -> dict[str, Any]:
    success = e.get("success")
    if success is True:
        st = "passed"
    elif success is False:
        st = "failed"
    else:
        st = "pending"
    out: dict[str, Any] = {
        "id": _s(e.get("id")),
        "roundNumber": int(e.get("attempt_number") or 0),
        "status": st,
        "startedAt": e.get("created_at"),
    }
    rj = e.get("result_json")
    summary = ""
    exit_code: int | None = None
    if isinstance(rj, dict):
        exit_code = rj.get("exit_code")
        if exit_code is None:
            exit_code = rj.get("exitCode")
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except (TypeError, ValueError):
                exit_code = None
        for key in ("summary", "message", "stdout_tail"):
            v = rj.get(key)
            if v:
                summary = str(v)[:4000]
                break
    if summary:
        out["summary"] = summary
    if exit_code is not None:
        out["exitCode"] = exit_code
    return out


def _map_review_ui_status(raw: Any, *, action_type: str | None = None) -> str:
    s = str(raw or "").lower().strip()
    action = str(action_type or "").lower().strip()
    if s in ("addressed", "dismissed", "open"):
        return s
    if s in ("applied", "recorded") and action == "approve":
        return "addressed"
    if s in ("closed", "resolved", "applied"):
        return "addressed"
    if s == "failed":
        return "dismissed"
    return "open"


def _format_review_for_ui(r: dict[str, Any]) -> dict[str, Any]:
    action = _s(r.get("action_type"))
    out: dict[str, Any] = {
        "id": _s(r.get("id")),
        "createdAt": r.get("created_at") or "",
        "instruction": _s(r.get("instruction_text")),
        "status": _map_review_ui_status(r.get("status"), action_type=action or None),
    }
    if action:
        out["actionType"] = action
    scope = r.get("target_scope")
    if scope:
        out["scope"] = str(scope)[:8000]
    return out


def build_session_detail_json_for_ui(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    """Full ``sessionDetailSchema`` object for GET/POST session flows."""
    sess = automation_session_service.get_session(db, session_id)
    if sess is None:
        raise KeyError("session_not_found")
    summary = automation_session_service.session_to_summary(db, sess)
    base = format_session_summary_for_ui(summary)
    bp = branch_policy_id_for_connection(db, repository_connection_id=summary.get("repository_connection_id"))
    if bp:
        base["branchPolicyId"] = bp

    rounds = [_format_round_for_ui(r) for r in automation_session_service.list_rounds_for_api(db, session_id)]
    patches = [_format_patch_for_ui(p) for p in automation_session_service.list_patch_versions_for_api(db, session_id)]
    executions = [_format_execution_for_ui(e) for e in automation_session_service.list_execution_attempts_for_api(db, session_id)]
    reviews = [_format_review_for_ui(r) for r in automation_session_service.list_review_requests_for_api(db, session_id)]

    pr_title: str | None = None
    pr_body: str | None = None
    pr_external_url: str | None = None
    pr_external_id: str | None = None
    pr_status: str | None = None
    latest_summary: str | None = None
    if executions:
        last = executions[-1]
        latest_summary = last.get("summary") if isinstance(last.get("summary"), str) else None
    pr_items = automation_pr_service.list_code_review_requests_for_api(db, session_id)
    if isinstance(pr_items, list) and pr_items:
        last_pr = pr_items[-1]
        if isinstance(last_pr, dict):
            pr_title = last_pr.get("title") or last_pr.get("pr_title")
            pr_body = last_pr.get("body") or last_pr.get("pr_body")
            pr_external_url = last_pr.get("external_url")
            pr_external_id = last_pr.get("external_id")
            pr_status = last_pr.get("status")
            if isinstance(pr_title, str):
                pr_title = pr_title[:512]
            if isinstance(pr_body, str):
                pr_body = pr_body[:8000]

    patch_summary: str | None = None
    if patches:
        patch_summary = f"{len(patches)} patch version(s)"

    out = {
        **base,
        "rounds": rounds,
        "patches": patches,
        "executions": executions,
        "reviews": reviews,
    }
    if latest_summary:
        out["latestExecutionSummary"] = latest_summary
    if patch_summary:
        out["patchSummary"] = patch_summary
    if pr_title:
        out["prPreviewTitle"] = pr_title
    if pr_body:
        out["prPreviewBody"] = pr_body
    if pr_external_url:
        out["prExternalUrl"] = str(pr_external_url)[:1024]
    if pr_external_id:
        out["prExternalId"] = str(pr_external_id)[:256]
    if pr_status:
        out["prStatus"] = str(pr_status)[:64]
    return out
