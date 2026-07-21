"""
UI dashboard contract for ``GET /api/v1/dashboard``.

Maps internal automation session/job statuses into the QSwarm Web Zod enum:
``draft | queued | running | awaiting_review | revising | succeeded | failed | cancelled``.

This module is BFF-only; core services and DB enums are unchanged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.db.models.repository_connection import RepositoryConnection
from app.services import automation_session_service

# Keys match the frontend Zod ``sessionCounts`` record (underscore slugs, not camelCase keys).
UI_SESSION_COUNT_KEYS: tuple[str, ...] = (
    "draft",
    "plan_ready",
    "queued",
    "running",
    "awaiting_review",
    "revising",
    "succeeded",
    "failed",
    "cancelled",
)


def empty_ui_session_counts() -> dict[str, int]:
    return {k: 0 for k in UI_SESSION_COUNT_KEYS}


_UI_DASHBOARD_STATUSES: frozenset[str] = frozenset(UI_SESSION_COUNT_KEYS)


def map_backend_to_ui_dashboard_status(summary: dict[str, Any]) -> str:
    """
    Map effective session summary (``session_to_summary``) to a single UI dashboard status.

    Uses both ``status`` (effective session enum) and ``job_status`` (raw job) where needed.
    """
    eff = str(summary.get("status") or "").strip()
    job = str(summary.get("job_status") or "").strip() if summary.get("job_status") else ""
    rnd = int(summary.get("current_round_number") or 0)

    if eff in ("pr_failed",) or job in ("pr_creation_failed", "failed"):
        return "failed"
    if eff == "pr_created" or job == "pr_created":
        return "succeeded"
    if job == "revising_after_review":
        return "revising"
    if eff == "approved_for_pr" or job == "approved_for_pr":
        return "queued"
    if eff == "creating_pr" or job == "creating_pr":
        return "running"
    if eff == "awaiting_review" or job in (
        "awaiting_automation_review",
        "awaiting_human_input",
        "awaiting_automation_approval",
    ):
        return "awaiting_review"
    if eff == "plan_ready" or job == "awaiting_plan_approval":
        return "plan_ready"
    if eff in ("planning", "generating", "executing"):
        return "running"
    if eff == "pending":
        if rnd == 0 and (not job or job == "pending"):
            return "draft"
        return "queued"
    if eff == "failed":
        return "failed"
    if eff in ("cancelled", "canceled"):
        return "cancelled"
    # Unknown internal states: bucket conservatively for the UI enum.
    return "queued"


def _str_field(val: Any, *, default: str = "") -> str:
    if val is None:
        return default
    return str(val).strip() if isinstance(val, str) else str(val)


def build_recent_session_row(session: AutomationSession, summary: dict[str, Any]) -> dict[str, Any]:
    """One ``recentSessions`` item: strings for id/engine/repoConnectionId/sourceRef; UI status enum only."""
    conn = summary.get("repository_connection_id")
    if conn is None and session.repository_connection_id is not None:
        conn = str(session.repository_connection_id)
    elif conn is not None:
        conn = str(conn).strip()

    return {
        "id": _str_field(summary.get("id")),
        "status": map_backend_to_ui_dashboard_status(summary),
        "engine": _str_field(summary.get("coding_engine"), default="stub"),
        "repo_connection_id": conn or "",
        "source_ref": _str_field(summary.get("source_reference")),
        "approved_case_id": _str_field(summary.get("approved_case_id")),
        "created_at": summary.get("created_at") or "",
        "updated_at": summary.get("updated_at") or "",
        "job_status": _str_field(summary.get("job_status")),
        "current_round_number": int(summary.get("current_round_number") or 0),
    }


def build_dashboard_response(db: Session, settings: Settings, *, scan_cap: int = 400, recent_limit: int = 12) -> dict[str, Any]:
    """
    Build an internal dashboard dict (snake keys for intermediate fields).

    ``session_counts`` uses UI enum strings as keys (underscore form). Caller should use
    :func:`format_dashboard_json_for_ui` for the wire JSON.
    """
    rows = list(
        db.scalars(
            select(AutomationSession)
            .options(joinedload(AutomationSession.automation_job))
            .order_by(AutomationSession.updated_at.desc())
            .limit(scan_cap)
        ).all()
    )

    counts = empty_ui_session_counts()
    recent_rows: list[dict[str, Any]] = []

    for s in rows:
        summ = automation_session_service.session_to_summary(db, s)
        ui_st = map_backend_to_ui_dashboard_status(summ)
        if ui_st not in _UI_DASHBOARD_STATUSES:
            ui_st = "queued"
        counts[ui_st] += 1

    for s in rows[:recent_limit]:
        summ = automation_session_service.session_to_summary(db, s)
        recent_rows.append(build_recent_session_row(s, summ))

    n_conn = int(db.scalar(select(func.count()).select_from(RepositoryConnection)) or 0)
    n_pol = int(db.scalar(select(func.count()).select_from(RepositoryBranchPolicy)) or 0)

    return {
        "session_counts": counts,
        "recent_sessions": recent_rows,
        "repository_connection_count": n_conn,
        "branch_policy_count": n_pol,
        "environment": settings.app_env,
        "application_name": settings.app_name,
    }


def format_dashboard_json_for_ui(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Final ``GET /api/v1/dashboard`` body: camelCase wrapper keys where the UI expects them,
    but **sessionCounts** keys and **recentSessions[].status** stay underscore enums (Zod).
    """
    counts = payload["session_counts"]
    recent_out: list[dict[str, Any]] = []
    for row in payload["recent_sessions"]:
        recent_out.append(
            {
                "id": row["id"],
                "status": row["status"],
                "engine": row["engine"],
                "repoConnectionId": row["repo_connection_id"],
                "sourceRef": row["source_ref"],
                "approvedCaseId": row.get("approved_case_id") or "",
                "createdAt": row.get("created_at") or "",
                "updatedAt": row.get("updated_at") or "",
                "jobStatus": row.get("job_status") or "",
                "currentRoundNumber": int(row.get("current_round_number") or 0),
            }
        )
    return {
        "sessionCounts": counts,
        "recentSessions": recent_out,
        "repositoryConnectionCount": int(payload["repository_connection_count"]),
        "branchPolicyCount": int(payload["branch_policy_count"]),
        "environment": payload["environment"],
        "applicationName": payload["application_name"],
    }
