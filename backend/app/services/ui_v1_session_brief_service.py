"""Session brief / plan preview payload for ``GET /api/v1/sessions/{id}/brief``."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.automation_job import AutomationJob
from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.db.models.repository_connection import RepositoryConnection
from app.db.models.test_case_record import TestCaseRecord
from app.services import automation_session_service
from app.services.ui_v1_branch_policies import branch_policy_id_for_connection, format_branch_policy_json_for_ui
from app.services.ui_v1_dashboard import map_backend_to_ui_dashboard_status
from app.services.ui_v1_mapper import dict_keys_to_camel
from app.services.ui_v1_session_review_service import _derive_next_actions


def _s(val: Any, *, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, str):
        return val.strip()
    return str(val)


def _str_list(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if x is not None and str(x).strip()]


def _build_source_summary(session_summary: dict[str, Any], job: AutomationJob | None) -> dict[str, Any]:
    case_spec = job.case_spec_json if job and isinstance(job.case_spec_json, dict) else {}
    case_input = job.case_input_json if job and isinstance(job.case_input_json, dict) else {}

    title = _s(case_spec.get("title")) or _s(case_input.get("case_title"))
    if not title:
        title = _s(session_summary.get("approved_case_id")) or _s(session_summary.get("source_reference"))

    case_id = _s(session_summary.get("approved_case_id"))
    if not case_id and job:
        case_id = _s(job.approved_case_id)

    description = _s(case_spec.get("description")) or _s(case_input.get("case_description"))
    objective = _s(case_spec.get("objective"))

    out: dict[str, Any] = {
        "source_system": _s(session_summary.get("source_system")),
        "source_reference": _s(session_summary.get("source_reference")),
        "case_id": case_id,
        "source_title": title[:512],
    }
    if description:
        out["description"] = description[:8000]
    if objective:
        out["objective"] = objective[:2000]
    steps = _str_list(case_spec.get("steps")) or _str_list(case_input.get("steps"))
    if steps:
        out["steps"] = steps[:50]
    expected = _str_list(case_spec.get("expected_results")) or _str_list(case_input.get("expected_results"))
    if expected:
        out["expected_results"] = expected[:50]
    pre = _str_list(case_spec.get("preconditions")) or _str_list(case_input.get("preconditions"))
    if pre:
        out["preconditions"] = pre[:30]
    notes = _str_list(case_spec.get("automation_notes"))
    if notes:
        out["automation_notes"] = notes[:20]
    missing = _str_list(case_spec.get("missing_information"))
    if missing:
        out["missing_information"] = missing[:20]
    return out


def _enrich_source_summary_from_registry(
    db: Session,
    session_summary: dict[str, Any],
    source_summary: dict[str, Any],
) -> dict[str, Any]:
    tcr_raw = session_summary.get("test_case_record_id")
    if not tcr_raw:
        return source_summary
    try:
        tcr_id = uuid.UUID(str(tcr_raw))
    except (ValueError, TypeError):
        return source_summary
    record = db.get(TestCaseRecord, tcr_id)
    if record is None:
        return source_summary
    source_summary["registry_key"] = record.registry_key
    source_summary["source_story_key"] = record.source_story_key
    if record.external_id:
        source_summary["published_test_case_id"] = record.external_id
    if record.external_url:
        source_summary["published_test_case_url"] = record.external_url
    source_summary["traceability_label"] = (
        f"Automating test case {record.external_id or record.registry_key} "
        f"from story {record.source_story_key}"
    )
    return source_summary


def _load_repository_connection(db: Session, conn_id: Any) -> RepositoryConnection | None:
    if conn_id is None:
        return None
    try:
        cid = uuid.UUID(str(conn_id))
    except (ValueError, TypeError):
        return None
    return db.get(RepositoryConnection, cid)


def _load_branch_policy(db: Session, conn_id: Any) -> dict[str, Any] | None:
    if conn_id is None:
        return None
    try:
        cid = uuid.UUID(str(conn_id))
    except (ValueError, TypeError):
        return None
    pol = db.scalar(
        select(RepositoryBranchPolicy)
        .where(RepositoryBranchPolicy.repository_connection_id == cid)
        .order_by(RepositoryBranchPolicy.updated_at.desc())
        .limit(1)
    )
    if pol is None:
        return None
    from app.api.routes import repository_connections as rc_routes

    return format_branch_policy_json_for_ui(rc_routes.branch_policy_to_response(pol))


def _build_setup(db: Session, session_summary: dict[str, Any], job: AutomationJob | None) -> dict[str, Any]:
    conn_id = session_summary.get("repository_connection_id")
    conn = _load_repository_connection(db, conn_id)
    policy = _load_branch_policy(db, conn_id)

    repo: dict[str, Any] = {
        "owner": _s(session_summary.get("repo_owner")),
        "name": _s(session_summary.get("repo_name")),
        "base_branch": _s(session_summary.get("base_branch"), default="main"),
    }
    if conn is not None:
        repo["display_name"] = _s(conn.display_name)
        repo["provider"] = _s(conn.provider)
        repo["default_branch"] = _s(conn.default_branch) or repo["base_branch"]
        if conn.clone_url:
            repo["clone_url_redacted"] = True

    out: dict[str, Any] = {
        "engine": _s(session_summary.get("coding_engine"), default="stub"),
        "repository_connection_id": _s(conn_id),
        "repository": repo,
        "branch_policy": policy,
        "branch_policy_id": branch_policy_id_for_connection(db, repository_connection_id=conn_id),
    }
    rp = _s(session_summary.get("repo_path"))
    if rp:
        out["workspace_configured"] = True
    return out


def _plan_from_sources(
    job: AutomationJob | None,
    plan_versions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, int, str]:
    current: dict[str, Any] | None = None
    for p in plan_versions:
        if p.get("is_current"):
            current = p
            break
    if current is None and plan_versions:
        current = plan_versions[-1]

    plan_json: dict[str, Any] | None = None
    version_n = 0
    version_id = ""
    if current and isinstance(current.get("plan_json"), dict):
        plan_json = dict(current["plan_json"])
        version_n = int(current.get("version_number") or 0)
        version_id = _s(current.get("id"))
    elif job and isinstance(job.change_plan_json, dict) and job.change_plan_json:
        plan_json = dict(job.change_plan_json)
    return plan_json, version_n, version_id


def _build_automation_brief(
    job: AutomationJob | None,
    plan_versions: list[dict[str, Any]],
) -> dict[str, Any]:
    plan_json, version_n, version_id = _plan_from_sources(job, plan_versions)
    fw = job.framework_summary_json if job and isinstance(job.framework_summary_json, dict) else {}
    repo_ctx = job.repo_context_json if job and isinstance(job.repo_context_json, dict) else {}

    if not plan_json and not fw and not repo_ctx:
        return {
            "available": False,
            "summary": "Automation plan will be generated when you start this run.",
        }

    brief: dict[str, Any] = {"available": True}
    if version_n:
        brief["plan_version"] = version_n
    if version_id:
        brief["plan_version_id"] = version_id

    framework_type = _s(plan_json.get("framework_type") if plan_json else "") or _s(fw.get("framework_type"))
    if framework_type:
        brief["framework_type"] = framework_type

    target = _s(plan_json.get("target_test_file") if plan_json else "")
    if target:
        brief["target_test_file"] = target

    if plan_json:
        for key in ("files_to_modify", "files_to_create", "files_to_reuse"):
            vals = plan_json.get(key)
            if isinstance(vals, list) and vals:
                brief[key] = [str(x) for x in vals[:30]]
        rationale = _s(plan_json.get("rationale")) or _s(plan_json.get("summary"))
        if rationale:
            brief["rationale"] = rationale[:4000]
        action = _s(plan_json.get("action_on_target_test_file"))
        if action:
            brief["action_on_target_test_file"] = action

    if fw:
        brief["framework_summary"] = {
            k: fw[k]
            for k in ("framework_type", "test_root", "runner_command", "detected_stack")
            if k in fw and fw[k] is not None
        }

    if repo_ctx:
        ctx_brief: dict[str, Any] = {}
        for key in ("framework_type", "selected_test_root", "related_page_objects", "similar_test_files"):
            if key in repo_ctx and repo_ctx[key] is not None:
                val = repo_ctx[key]
                if isinstance(val, list):
                    ctx_brief[key] = val[:15]
                    ctx_brief[f"{key}_count"] = len(val)
                else:
                    ctx_brief[key] = val
        if ctx_brief:
            brief["repo_context_summary"] = ctx_brief

    if not brief.get("rationale") and target:
        brief["summary"] = f"QSwarm will automate {target} using the {framework_type or 'detected'} test stack."
    elif not brief.get("summary"):
        brief["summary"] = "Change plan is ready for automation execution."

    return brief


def build_session_brief_for_ui(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    """
    Product-facing session brief (snake_case internally; router returns camelCase).

    Raises:
        KeyError: session_not_found
    """
    sess = automation_session_service.get_session(db, session_id)
    if sess is None:
        raise KeyError("session_not_found")

    summary = automation_session_service.session_to_summary(db, sess)
    job = db.get(AutomationJob, sess.automation_job_id) if sess.automation_job_id else None
    plan_versions = automation_session_service.list_plan_versions_for_api(db, session_id)

    review_state = map_backend_to_ui_dashboard_status(summary)
    workflow_status = _s(summary.get("status"))

    payload = {
        "session_id": str(session_id),
        "session_state": {
            "status": review_state,
            "workflow_status": workflow_status,
            "job_status": _s(summary.get("job_status")),
            "current_round_number": int(summary.get("current_round_number") or 0),
            "plan_approved": bool(summary.get("plan_approved_at")),
            "plan_approved_at": summary.get("plan_approved_at") or "",
            "next_actions": _derive_next_actions(summary),
            "created_at": summary.get("created_at") or "",
            "updated_at": summary.get("updated_at") or "",
        },
        "source_summary": _enrich_source_summary_from_registry(
            db,
            summary,
            _build_source_summary(summary, job),
        ),
        "setup": _build_setup(db, summary, job),
        "automation_brief": _build_automation_brief(job, plan_versions),
    }
    return dict_keys_to_camel(payload)
