"""UI-facing routes under ``/api/v1``.

**Backend-first:** CRUD that mirrors legacy services uses ``dict_keys_to_camel(model_dump())``
only — no per-endpoint compatibility mappers (see ``docs/UI_V1_API_CLASSIFICATION.md``).

**BFF:** Dashboard and session/branch flows that aggregate or reshape data for the UI.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import DbSession
from app.api.routes import automation_sessions as as_routes
from app.api.routes import repository_connections as rc_routes
from app.core.config import get_settings
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.db.models.repository_connection import RepositoryConnection
from app.schemas.automation_session import (
    AutomationSessionApproveBody,
    AutomationSessionRevisionBody,
)
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.repository_connection import AutomationSessionCreatePrBody
from app.schemas.ui_v1_models import (
    UiAutomationSessionApprove,
    UiAutomationSessionCreate,
    UiAutomationSessionCreatePr,
    UiAutomationSessionRevision,
    UiAutomationSessionStart,
    UiBranchPolicyCreate,
    UiBranchPolicyPatch,
    UiRepositoryConnectionCreate,
    UiRepositoryConnectionPatch,
)
from app.services import automation_session_service
from app.services.ui_v1_branch_policies import format_branch_policy_json_for_ui
from app.services.ui_v1_dashboard import build_dashboard_response, format_dashboard_json_for_ui, map_backend_to_ui_dashboard_status
from app.services.ui_v1_mapper import dict_keys_to_camel
from app.services.ui_v1_session_review_service import build_session_review_data_for_ui
from app.services.ui_v1_sessions import build_session_detail_json_for_ui, format_session_summary_for_ui

router = APIRouter(prefix="/api/v1", tags=["ui-v1"])


def _camel_json(data: Any) -> Any:
    return dict_keys_to_camel(data)


# --- dashboard ---


@router.get("/dashboard")
def ui_dashboard(db: DbSession):
    """Aggregated dashboard for QSwarm Web; see ``app.services.ui_v1_dashboard`` for Zod-aligned normalization."""
    return format_dashboard_json_for_ui(build_dashboard_response(db, get_settings()))


# --- settings ---


@router.get("/settings")
def ui_settings():
    """Backend config slice for the UI (camelCase); same facts as server settings, no synthetic nested product schema."""
    s = get_settings()
    return _camel_json(
        {
            "application_name": s.app_name,
            "environment": s.app_env,
            "debug": s.app_debug,
            "jira": {"use_stub": s.jira_use_stub, "configured": s.jira_configured},
            "coding_provider": s.coding_provider,
            "workspace_root": s.qswarm_workspace_root,
            "claude_code_enabled": s.qswarm_claude_code_enabled,
            "copilot_agent_enabled": s.qswarm_copilot_agent_enabled,
            "notes": "Read-only slice for the UI; secrets are never returned.",
        }
    )


# --- repo connections ---


@router.get("/repo-connections")
def ui_list_repo_connections(db: DbSession):
    """Same contract as ``GET /repo-connections`` (``items`` list), camelCase keys for JSON."""
    res = rc_routes.list_repo_connections(db)
    return _camel_json(res.model_dump())


@router.get("/repo-connections/{connection_id}", responses={404: {"model": ErrorResponse}})
def ui_get_repo_connection(connection_id: uuid.UUID, db: DbSession):
    row = rc_routes.get_repo_connection(connection_id, db)
    return _camel_json(row.model_dump())


@router.post("/repo-connections", status_code=status.HTTP_201_CREATED, responses={400: {"model": ErrorResponse}})
def ui_create_repo_connection(body: UiRepositoryConnectionCreate, db: DbSession):
    row = rc_routes.create_repo_connection(body.to_legacy(), db)
    return _camel_json(row.model_dump())


@router.patch("/repo-connections/{connection_id}", responses={404: {"model": ErrorResponse}})
def ui_patch_repo_connection(connection_id: uuid.UUID, body: UiRepositoryConnectionPatch, db: DbSession):
    row = rc_routes.patch_repo_connection(connection_id, body.to_legacy(), db)
    return _camel_json(row.model_dump())


# --- branch policies (by policy id; create requires repositoryConnectionId) ---


@router.get("/branch-policies")
def ui_list_branch_policies(db: DbSession):
    """Top-level JSON array of ``branchPolicySchema`` rows (Qswarm-UI)."""
    rows = list(
        db.scalars(select(RepositoryBranchPolicy).order_by(RepositoryBranchPolicy.updated_at.desc())).all()
    )
    return [format_branch_policy_json_for_ui(rc_routes.branch_policy_to_response(p)) for p in rows]


@router.get("/branch-policies/{policy_id}", responses={404: {"model": ErrorResponse}})
def ui_get_branch_policy(policy_id: uuid.UUID, db: DbSession):
    p = db.get(RepositoryBranchPolicy, policy_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Branch policy not found").model_dump(),
        )
    return format_branch_policy_json_for_ui(rc_routes.branch_policy_to_response(p))


@router.post("/branch-policies", status_code=status.HTTP_201_CREATED, responses={404: {"model": ErrorResponse}})
def ui_create_branch_policy(body: UiBranchPolicyCreate, db: DbSession):
    row = rc_routes.create_branch_policy(body.repository_connection_id, body.to_legacy(), db)
    return format_branch_policy_json_for_ui(row)


@router.patch("/branch-policies/{policy_id}", responses={404: {"model": ErrorResponse}})
def ui_patch_branch_policy(policy_id: uuid.UUID, body: UiBranchPolicyPatch, db: DbSession):
    p = db.get(RepositoryBranchPolicy, policy_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Branch policy not found").model_dump(),
        )
    data = body.to_legacy().model_dump(exclude_unset=True)
    if "base_branch_default" in data and data["base_branch_default"]:
        p.base_branch_default = data["base_branch_default"].strip()[:256]
    if "branch_naming_pattern" in data and data["branch_naming_pattern"]:
        p.branch_naming_pattern = data["branch_naming_pattern"].strip()[:512]
    if "pr_title_template" in data:
        p.pr_title_template = (
            data["pr_title_template"].strip()[:512] if data.get("pr_title_template") else None
        )
    if "pr_body_template" in data:
        p.pr_body_template = data["pr_body_template"]
    if body.repository_connection_id is not None:
        p.repository_connection_id = body.repository_connection_id
    db.commit()
    db.refresh(p)
    return format_branch_policy_json_for_ui(rc_routes.branch_policy_to_response(p))


# --- sessions ---


def _list_session_summaries(db: Session, *, status: str | None, limit: int) -> list[dict[str, Any]]:
    lim = max(1, min(limit, 200))
    rows = list(
        db.scalars(
            select(AutomationSession)
            .options(joinedload(AutomationSession.automation_job))
            .order_by(AutomationSession.updated_at.desc())
            .limit(lim * 3)
        ).all()
    )
    out: list[dict[str, Any]] = []
    for s in rows:
        summ = automation_session_service.session_to_summary(db, s)
        if status and map_backend_to_ui_dashboard_status(summ) != status:
            continue
        out.append(format_session_summary_for_ui(summ))
        if len(out) >= lim:
            break
    return out


@router.get("/sessions")
def ui_list_sessions(
    db: DbSession,
    status: str | None = Query(default=None, description="Filter by UI session status (Zod enum)"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Top-level JSON array of ``sessionSummarySchema`` rows (Qswarm-UI)."""
    return _list_session_summaries(db, status=status, limit=limit)


@router.get("/sessions/{session_id}/review-data", responses={404: {"model": ErrorResponse}})
def ui_get_session_review_data(session_id: uuid.UUID, db: DbSession):
    """Review cockpit payload: changed files, before/after content, conversation timeline, PR info."""
    try:
        return build_session_review_data_for_ui(db, session_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        ) from None


@router.get("/sessions/{session_id}", responses={404: {"model": ErrorResponse}})
def ui_get_session_detail(session_id: uuid.UUID, db: DbSession):
    try:
        return build_session_detail_json_for_ui(db, session_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        ) from None


@router.post("/sessions", status_code=status.HTTP_201_CREATED, responses={400: {"model": ErrorResponse}})
def ui_create_session(body: UiAutomationSessionCreate, db: DbSession):
    res = as_routes.create_session(body.to_legacy(db=db), db)
    return build_session_detail_json_for_ui(db, uuid.UUID(res.id))


@router.post(
    "/sessions/{session_id}/start",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
def ui_start_session(session_id: uuid.UUID, db: DbSession, body: UiAutomationSessionStart | None = None):
    legacy = (body or UiAutomationSessionStart()).to_legacy()
    as_routes.start_session(session_id, db, legacy)
    return build_session_detail_json_for_ui(db, session_id)


@router.post(
    "/sessions/{session_id}/request-revision",
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def ui_request_revision(session_id: uuid.UUID, body: UiAutomationSessionRevision, db: DbSession):
    actor_id, instruction_text, target_scope = body.to_legacy_tuple()
    legacy = AutomationSessionRevisionBody(
        actor_id=actor_id, instruction_text=instruction_text, target_scope=target_scope
    )
    as_routes.request_revision(session_id, legacy, db)
    return build_session_detail_json_for_ui(db, session_id)


@router.post("/sessions/{session_id}/approve", responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
def ui_approve_session(session_id: uuid.UUID, body: UiAutomationSessionApprove, db: DbSession):
    legacy = AutomationSessionApproveBody(actor_id=body.to_legacy_actor())
    as_routes.approve_session(session_id, legacy, db)
    return build_session_detail_json_for_ui(db, session_id)


@router.post(
    "/sessions/{session_id}/create-pr",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
def ui_create_pr(session_id: uuid.UUID, body: UiAutomationSessionCreatePr, db: DbSession):
    legacy = AutomationSessionCreatePrBody(
        actor_id=body.actor_id,
        repository_connection_id=body.repository_connection_id,
        target_branch=body.target_branch,
        source_branch=body.source_branch,
        title_override=body.title_override,
        body_override=body.body_override,
    )
    as_routes.create_pr_for_session(session_id, legacy, db)
    return build_session_detail_json_for_ui(db, session_id)
