"""Sprint 2 automation session orchestration (control plane over AutomationJob)."""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.automation_engine import CodeSessionContext
from app.automation_engine.coding_engine_names import CodingEngineName
from app.automation_engine.engine_errors import (
    EngineAdapterError,
    EngineConfigurationError,
    EngineMalformedOutputError,
    EngineRepoAccessError,
    EngineTimeoutError,
)
from app.automation_engine.registry import resolve_coding_agent_adapter
from app.core.config import get_settings
from app.core.constants import (
    ActorType,
    AuditEventType,
    AutomationJobStatus,
    AutomationReviewRequestAction,
    AutomationReviewRequestStatus,
    AutomationRevisionRoundTrigger,
    AutomationSessionStatus,
)
from app.db.models.automation_execution_attempt import AutomationExecutionAttempt
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_patch_version import AutomationPatchVersion
from app.db.models.automation_plan_version import AutomationPlanVersion
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_review_request import AutomationReviewRequest
from app.db.models.automation_session import AutomationSession
from app.schemas.automation import AutomationJobCreateRequest
from app.schemas.automation_session import AutomationSessionCreateRequest
from app.services import audit_service, automation_job_service
from app.services.automation_job_service import ChangePlanRejected, PatchRejected, WorkspaceApplyRejected
from app.services.automation_engine_payload_builder import AutomationEnginePayloadBuilder
from app.services.automation_session_review_state import (
    build_session_approve_state_error_message,
    reconcile_job_for_session_approve,
)
from app.services.execution_service import resolve_target_test_file
from app.services.framework_scan_service import FrameworkScanError
from app.services.repository_connection_service import get_repository_connection
from app.services.framework_runtime_errors import (
    FrameworkDetectionError,
    HostedExecutionPreparationError,
    PlaywrightBrowserPreparationError,
    RuntimeValidationError,
    UnsupportedHostedFrameworkError,
)
from app.services.framework_runtime_service import (
    build_repo_bootstrap_plan,
    detect_framework_runtime,
    prepare_hosted_materialized_execution,
)
from app.services.repo_bootstrap_service import (
    RepoBootstrapError,
    WorkspaceProfile,
    bootstrap_node_workspace,
    bootstrap_result_to_audit_payload,
    planned_npm_bootstrap_command,
)
from app.services.repo_workspace_service import (
    RepoAuthError,
    RepoWorkspaceError,
    prepare_automation_session_workspace,
    resolve_workspace_bootstrap_profile,
)
from app.services.workspace_cache_service import record_workspace_cache_after_hosted_materialize
from app.services.workspace_material_change_service import (
    RevisionNoMaterialChangeError,
    capture_workspace_snapshot,
    require_material_workspace_change,
    resolve_revision_scoped_paths,
    resolve_revision_workspace_root,
)


logger = logging.getLogger(__name__)


def _run_repo_bootstrap_for_session(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    actor_id: str,
    workspace_profile: WorkspaceProfile,
    prep_mode: str | None = None,
) -> None:
    rp = (job.repo_path or session.repo_path or "").strip()
    if not rp:
        raise RepoBootstrapError(
            "Workspace path is missing after preparation; dependency bootstrap cannot run "
            "and execution must not proceed.",
            code="repo_bootstrap_workspace_path_missing",
        )
    root = Path(rp)

    if workspace_profile == "hosted_materialized":
        profile = detect_framework_runtime(root)
        plan = build_repo_bootstrap_plan(profile, root)
        planned_cmd = plan.command
        stack = plan.strategy_key
    else:
        profile = None
        plan = None
        planned_cmd, stack = planned_npm_bootstrap_command(root)

    start_payload: dict[str, Any] = {
        "workspace_path": rp,
        "workspace_profile": workspace_profile,
        "prep_mode": prep_mode,
        "detected_stack": stack,
        "planned_command": planned_cmd,
    }
    if profile is not None:
        start_payload["framework_runtime_profile"] = profile.to_audit_dict()
    if plan is not None:
        start_payload["repo_bootstrap_plan"] = {
            "command": plan.command,
            "required": plan.required,
            "strategy_key": plan.strategy_key,
            "validation_paths": list(plan.validation_paths),
            "notes": plan.notes,
        }
    if profile is not None and profile.framework_name == "playwright":
        start_payload["planned_playwright_chromium_install"] = ["npx", "playwright", "install", "chromium"]
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REPO_BOOTSTRAP_STARTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=(actor_id or "system")[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="repo_bootstrap",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload=start_payload,
    )
    db.flush()
    try:
        if workspace_profile == "hosted_materialized":
            hosted = prepare_hosted_materialized_execution(root, settings=get_settings())
            res = hosted.bootstrap_result
            done_payload = bootstrap_result_to_audit_payload(res)
            done_payload["framework_runtime_profile"] = hosted.profile.to_audit_dict()
            done_payload["runtime_validation"] = hosted.runtime_validation.to_audit_dict()
            if hosted.browser_preparation is not None:
                done_payload["playwright_browser_preparation"] = hosted.browser_preparation.to_audit_dict()
        else:
            res = bootstrap_node_workspace(
                root,
                workspace_profile=workspace_profile,
                settings=get_settings(),
            )
            done_payload = bootstrap_result_to_audit_payload(res)
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPO_BOOTSTRAP.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=(actor_id or "system")[:256],
            workflow_run_id=session.workflow_run_id,
            step_name="repo_bootstrap",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload=done_payload,
        )
        db.flush()
    except (RepoBootstrapError, HostedExecutionPreparationError) as e:
        fail_payload: dict[str, Any] = {"success": False, "code": e.code, "message": e.message[:4000]}
        if isinstance(e, RuntimeValidationError):
            det = getattr(e, "details", None)
            if isinstance(det, dict) and det:
                fail_payload["runtime_validation_details"] = det
        if isinstance(e, PlaywrightBrowserPreparationError):
            det = getattr(e, "details", None)
            if isinstance(det, dict) and det:
                fail_payload["playwright_browser_prep_details"] = det
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_REPO_BOOTSTRAP.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=(actor_id or "system")[:256],
            workflow_run_id=session.workflow_run_id,
            step_name="repo_bootstrap",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload=fail_payload,
        )
        db.flush()
        log_key = (
            "hosted_execution_preparation_failed"
            if isinstance(e, HostedExecutionPreparationError)
            else "repo_bootstrap_failed"
        )
        logger.warning(
            log_key,
            extra={"automation_job_id": str(job.id), "code": e.code, "workspace_profile": workspace_profile},
        )
        raise


def _map_job_status_to_session(job_status: str) -> AutomationSessionStatus:
    j = AutomationJobStatus
    m: dict[str, AutomationSessionStatus] = {
        j.PENDING.value: AutomationSessionStatus.PENDING,
        j.SCANNING_FRAMEWORK.value: AutomationSessionStatus.PLANNING,
        j.COLLECTING_CONTEXT.value: AutomationSessionStatus.PLANNING,
        j.PLANNING_CHANGES.value: AutomationSessionStatus.PLANNING,
        j.AWAITING_PLAN_APPROVAL.value: AutomationSessionStatus.PLAN_READY,
        j.GENERATING_CODE.value: AutomationSessionStatus.GENERATING,
        j.APPLYING_CHANGES.value: AutomationSessionStatus.GENERATING,
        j.EXECUTING.value: AutomationSessionStatus.EXECUTING,
        j.REVISING_AFTER_REVIEW.value: AutomationSessionStatus.EXECUTING,
        j.REPAIRING.value: AutomationSessionStatus.EXECUTING,
        j.AWAITING_AUTOMATION_REVIEW.value: AutomationSessionStatus.AWAITING_REVIEW,
        j.AWAITING_HUMAN_INPUT.value: AutomationSessionStatus.AWAITING_REVIEW,
        j.AWAITING_AUTOMATION_APPROVAL.value: AutomationSessionStatus.AWAITING_REVIEW,
        j.APPROVED_FOR_PR.value: AutomationSessionStatus.APPROVED_FOR_PR,
        j.CREATING_PR.value: AutomationSessionStatus.CREATING_PR,
        j.PR_CREATED.value: AutomationSessionStatus.PR_CREATED,
        j.PR_CREATION_FAILED.value: AutomationSessionStatus.PR_FAILED,
        j.FAILED.value: AutomationSessionStatus.FAILED,
    }
    return m.get(job_status, AutomationSessionStatus.PENDING)


def sync_session_status_from_job(session: AutomationSession, job: AutomationJob) -> None:
    session.status = _map_job_status_to_session(job.status).value


def _session_start_pre_round_failure_stage(exc: BaseException) -> str:
    """Classify where start failed before the first revision round is created."""
    if isinstance(exc, RepoAuthError):
        return "workspace_clone_auth"
    if isinstance(exc, RepoWorkspaceError):
        return "workspace_prep"
    if isinstance(exc, RepoBootstrapError):
        if getattr(exc, "code", "") == "repo_bootstrap_workspace_path_missing":
            return "workspace_prep"
        return "bootstrap"
    if isinstance(exc, RuntimeValidationError):
        return "runtime_validation"
    if isinstance(exc, PlaywrightBrowserPreparationError):
        return "playwright_browser_prep"
    if isinstance(exc, FrameworkDetectionError):
        return "framework_detection"
    if isinstance(exc, UnsupportedHostedFrameworkError):
        return "framework_detection"
    if isinstance(exc, HostedExecutionPreparationError):
        return "framework_runtime"
    return "unknown"


def _persist_session_start_pre_round_failure(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    actor_id: str,
    exc: BaseException,
    stage: str,
) -> None:
    """Mark session + job failed and audit when start fails before round 1 is created."""
    msg = getattr(exc, "message", None) or str(exc)
    code = getattr(exc, "code", None) or "unknown_error"
    job.status = AutomationJobStatus.FAILED.value
    job.blocked_reason = (msg[:2048] if msg else None)
    sync_session_status_from_job(session, job)
    audit_payload: dict[str, Any] = {
        "automation_session_id": str(session.id),
        "automation_job_id": str(job.id),
        "stage": stage,
        "code": code,
        "message": (msg or "")[:4000],
    }
    if isinstance(exc, RuntimeValidationError):
        det = getattr(exc, "details", None)
        if isinstance(det, dict) and det:
            audit_payload["runtime_validation_details"] = det
    if isinstance(exc, PlaywrightBrowserPreparationError):
        det = getattr(exc, "details", None)
        if isinstance(det, dict) and det:
            audit_payload["playwright_browser_prep_details"] = det
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_SESSION_START_PRE_ROUND_FAILED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_start",
        entity_type="automation_session",
        entity_id=str(session.id),
        payload=audit_payload,
    )
    db.flush()
    logger.warning(
        "automation_session_start_pre_round_failed",
        extra={
            "automation_session_id": str(session.id),
            "automation_job_id": str(job.id),
            "stage": stage,
            "code": code,
            "job_repo_path": job.repo_path,
            "session_repo_path": session.repo_path,
        },
    )


def _next_plan_version_number(db: Session, session_id: uuid.UUID) -> int:
    m = db.scalar(
        select(func.max(AutomationPlanVersion.version_number)).where(
            AutomationPlanVersion.automation_session_id == session_id
        )
    )
    return int(m or 0) + 1


def _next_patch_version_number(db: Session, session_id: uuid.UUID) -> int:
    m = db.scalar(
        select(func.max(AutomationPatchVersion.version_number)).where(
            AutomationPatchVersion.automation_session_id == session_id
        )
    )
    return int(m or 0) + 1


def _next_execution_attempt_number(db: Session, session_id: uuid.UUID) -> int:
    m = db.scalar(
        select(func.max(AutomationExecutionAttempt.attempt_number)).where(
            AutomationExecutionAttempt.automation_session_id == session_id
        )
    )
    return int(m or 0) + 1


def _clear_current_plan_flags(db: Session, session_id: uuid.UUID) -> None:
    for row in db.scalars(
        select(AutomationPlanVersion).where(AutomationPlanVersion.automation_session_id == session_id)
    ).all():
        row.is_current = False


def _clear_current_patch_flags(db: Session, session_id: uuid.UUID) -> None:
    for row in db.scalars(
        select(AutomationPatchVersion).where(AutomationPatchVersion.automation_session_id == session_id)
    ).all():
        row.is_current = False


def record_plan_version(
    db: Session,
    *,
    session: AutomationSession,
    revision_round: AutomationRevisionRound,
    plan_json: dict[str, Any],
    created_by: str,
) -> AutomationPlanVersion:
    _clear_current_plan_flags(db, session.id)
    vn = _next_plan_version_number(db, session.id)
    row = AutomationPlanVersion(
        automation_session_id=session.id,
        revision_round_id=revision_round.id,
        version_number=vn,
        plan_json=plan_json,
        is_current=True,
        created_by=created_by[:256],
    )
    db.add(row)
    db.flush()
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_PLAN_VERSION_CREATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=created_by[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session",
        entity_type="automation_plan_version",
        entity_id=str(row.id),
        payload={"automation_session_id": str(session.id), "version_number": vn},
    )
    db.flush()
    return row


def _hydrate_patch_json_for_version_storage(job: AutomationJob, summary_patch: dict[str, Any]) -> dict[str, Any]:
    """
    Persist full ``generated_files[].content`` alongside summarized metadata.

    ``job.generated_patch_json`` uses :func:`summarize_patch_for_persistence` (no file bodies).
    Hosted create-pr rebuild reads ``AutomationPatchVersion.patch_json`` as source of truth, so we
    snapshot post-apply workspace contents for each listed path.
    """
    merged = copy.deepcopy(summary_patch)
    rp = (job.repo_path or "").strip()
    if not rp:
        return merged
    try:
        root = Path(rp).expanduser().resolve()
    except OSError:
        return merged
    hydrated: list[dict[str, Any]] = []
    for it in merged.get("generated_files") or []:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("path", "")).strip().replace("\\", "/")
        if not rel or ".." in rel or rel.startswith("/"):
            continue
        action = str(it.get("action") or "modify").strip()
        try:
            dest = (root / rel).resolve()
            dest.relative_to(root)
        except (OSError, ValueError):
            continue
        if not dest.is_file():
            continue
        try:
            txt = dest.read_text(encoding="utf-8")
        except OSError:
            continue
        hydrated.append({"path": rel, "action": action, "content": txt})
    merged["generated_files"] = hydrated
    return merged


def record_patch_version(
    db: Session,
    *,
    session: AutomationSession,
    revision_round: AutomationRevisionRound,
    patch_json: dict[str, Any],
    created_by: str,
) -> AutomationPatchVersion:
    _clear_current_patch_flags(db, session.id)
    vn = _next_patch_version_number(db, session.id)
    row = AutomationPatchVersion(
        automation_session_id=session.id,
        revision_round_id=revision_round.id,
        version_number=vn,
        patch_json=patch_json,
        is_current=True,
        created_by=created_by[:256],
    )
    db.add(row)
    db.flush()
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_PATCH_VERSION_CREATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=created_by[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session",
        entity_type="automation_patch_version",
        entity_id=str(row.id),
        payload={"automation_session_id": str(session.id), "version_number": vn},
    )
    db.flush()
    return row


def record_execution_attempt(
    db: Session,
    *,
    session: AutomationSession,
    revision_round: AutomationRevisionRound | None,
    job: AutomationJob,
) -> AutomationExecutionAttempt:
    ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
    an = _next_execution_attempt_number(db, session.id)
    cmd = ex.get("command")
    if cmd is not None and not isinstance(cmd, (dict, list)):
        cmd = None
    row = AutomationExecutionAttempt(
        automation_session_id=session.id,
        revision_round_id=revision_round.id if revision_round else None,
        attempt_number=an,
        target_test_file=str(ex.get("target_test_file") or resolve_target_test_file(job) or "")[:1024]
        or None,
        command_json=cmd if isinstance(cmd, (dict, list)) else None,
        result_json=ex,
        success=bool(ex.get("success")),
    )
    db.add(row)
    db.flush()
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_EXECUTION_ATTEMPT_RECORDED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id="automation_session",
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session",
        entity_type="automation_execution_attempt",
        entity_id=str(row.id),
        payload={"automation_session_id": str(session.id), "attempt_number": an},
    )
    db.flush()
    return row


def create_automation_session(db: Session, body: AutomationSessionCreateRequest) -> AutomationSession:
    try:
        CodingEngineName.parse(body.coding_engine)
    except ValueError as e:
        raise ValueError(str(e)) from e

    if body.repository_connection_id is not None:
        rc = get_repository_connection(db, body.repository_connection_id)
        if rc is None or not rc.is_active:
            raise ValueError("repository_connection_invalid")
    else:
        rc = None

    ro = (body.repo_owner or "").strip() or None
    rn = (body.repo_name or "").strip() or None
    if rc is not None:
        if not ro:
            ro = (rc.owner_or_org or "").strip() or None
        if not rn:
            rn = (rc.repo_name or "").strip() or None

    job_body = AutomationJobCreateRequest(
        approved_case_id=body.approved_case_id,
        requested_by=body.created_by,
        repo_id=body.repo_id,
        repo_owner=ro,
        repo_name=rn,
        repo_path=body.repo_path,
        base_branch=body.base_branch,
        workflow_run_id=body.workflow_run_id,
        case_title=body.case_title,
        case_description=body.case_description,
        preconditions=body.preconditions,
        steps=body.steps,
        expected_results=body.expected_results,
    )
    job = automation_job_service.create_automation_job(db, job_body)

    sess = AutomationSession(
        source_system=body.source_system.strip() if body.source_system else None,
        source_reference=body.source_reference.strip() if body.source_reference else None,
        automation_job_id=job.id,
        repo_owner=job.repo_owner,
        repo_name=job.repo_name,
        repo_path=job.repo_path,
        base_branch=job.base_branch,
        coding_engine=body.coding_engine.strip().lower(),
        status=AutomationSessionStatus.PENDING.value,
        current_round_number=0,
        approved_case_id=job.approved_case_id,
        workflow_run_id=body.workflow_run_id,
        created_by=body.created_by.strip(),
        repository_connection_id=body.repository_connection_id,
    )
    db.add(sess)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_SESSION_CREATED.value,
        actor_type=ActorType.USER.value,
        actor_id=body.created_by.strip()[:256],
        workflow_run_id=body.workflow_run_id,
        step_name="automation_session",
        entity_type="automation_session",
        entity_id=str(sess.id),
        payload={"automation_job_id": str(job.id), "coding_engine": sess.coding_engine},
    )
    db.flush()
    return sess


def get_session(db: Session, session_id: uuid.UUID) -> AutomationSession | None:
    return db.get(AutomationSession, session_id)


def session_to_summary(db: Session, session: AutomationSession) -> dict[str, Any]:
    job = db.get(AutomationJob, session.automation_job_id) if session.automation_job_id else None
    effective_status = (
        _map_job_status_to_session(job.status).value if job else session.status
    )
    return {
        "id": str(session.id),
        "source_system": session.source_system,
        "source_reference": session.source_reference,
        "automation_job_id": str(session.automation_job_id) if session.automation_job_id else None,
        "repo_owner": session.repo_owner,
        "repo_name": session.repo_name,
        "repo_path": session.repo_path,
        "repository_connection_id": str(session.repository_connection_id)
        if session.repository_connection_id
        else None,
        "base_branch": session.base_branch,
        "coding_engine": session.coding_engine,
        "status": effective_status,
        "current_round_number": session.current_round_number,
        "approved_case_id": session.approved_case_id,
        "workflow_run_id": str(session.workflow_run_id) if session.workflow_run_id else None,
        "created_by": session.created_by,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "job_status": job.status if job else None,
        "plan_approved_at": session.plan_approved_at.isoformat() if session.plan_approved_at else None,
    }


def _get_initial_round_in_progress(db: Session, session_id: uuid.UUID) -> AutomationRevisionRound | None:
    return db.scalar(
        select(AutomationRevisionRound)
        .where(
            AutomationRevisionRound.automation_session_id == session_id,
            AutomationRevisionRound.round_number == 1,
            AutomationRevisionRound.trigger_type == AutomationRevisionRoundTrigger.INITIAL.value,
            AutomationRevisionRound.status == "in_progress",
        )
        .limit(1)
    )


def _prepare_session_workspace_for_start(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    actor_id: str,
    repository_connection_id: uuid.UUID | None,
) -> None:
    aid = actor_id
    try:
        prep = prepare_automation_session_workspace(
            db,
            session=session,
            job=job,
            repository_connection_id=repository_connection_id,
            settings=get_settings(),
        )
        record_workspace_cache_after_hosted_materialize(
            db,
            session=session,
            job=job,
            prep=prep,
            repository_connection_id=repository_connection_id,
            settings=get_settings(),
        )
        profile = resolve_workspace_bootstrap_profile(session, job, prep=prep, settings=get_settings())
        _run_repo_bootstrap_for_session(
            db,
            session=session,
            job=job,
            actor_id=aid,
            workspace_profile=profile,
            prep_mode=prep.mode,
        )
    except (RepoAuthError, RepoWorkspaceError, RepoBootstrapError, HostedExecutionPreparationError) as e:
        stage = _session_start_pre_round_failure_stage(e)
        _persist_session_start_pre_round_failure(
            db, session=session, job=job, actor_id=aid, exc=e, stage=stage
        )
        raise


def _record_round_artifacts(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    rnd: AutomationRevisionRound,
    actor_id: str,
    include_plan: bool = True,
    include_patch: bool = True,
    include_execution: bool = True,
) -> None:
    aid = actor_id
    if include_plan and isinstance(job.change_plan_json, dict) and job.change_plan_json:
        record_plan_version(
            db, session=session, revision_round=rnd, plan_json=dict(job.change_plan_json), created_by=aid
        )
    if include_patch and isinstance(job.generated_patch_json, dict) and job.generated_patch_json:
        record_patch_version(
            db,
            session=session,
            revision_round=rnd,
            patch_json=_hydrate_patch_json_for_version_storage(job, dict(job.generated_patch_json)),
            created_by=aid,
        )
    if include_execution and isinstance(job.execution_result_json, dict) and job.execution_result_json:
        record_execution_attempt(db, session=session, revision_round=rnd, job=job)


def _handle_initial_engine_errors(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    rnd: AutomationRevisionRound,
) -> None:
    rnd.status = "failed"
    session.current_round_number = max(session.current_round_number, 1)
    db.refresh(job)
    sync_session_status_from_job(session, job)
    db.flush()


def prepare_automation_session_plan(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str | None = None,
    repository_connection_id: uuid.UUID | None = None,
) -> AutomationSession:
    """Workspace prep + change planning; pauses at plan approval before code generation."""
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    if not session.automation_job_id:
        raise ValueError("session_missing_job")
    job = db.get(AutomationJob, session.automation_job_id)
    if job is None:
        raise ValueError("job_not_found")

    if job.status == AutomationJobStatus.AWAITING_PLAN_APPROVAL.value:
        sync_session_status_from_job(session, job)
        return session

    if session.current_round_number > 0:
        raise ValueError("session_already_started")
    if job.status != AutomationJobStatus.PENDING.value:
        raise ValueError("job_not_pending")

    aid = (actor_id or session.created_by or "").strip()
    if not aid:
        raise ValueError("actor_missing")

    _prepare_session_workspace_for_start(
        db,
        session=session,
        job=job,
        actor_id=aid,
        repository_connection_id=repository_connection_id,
    )

    adapter = resolve_coding_agent_adapter(session.coding_engine)
    rnd = AutomationRevisionRound(
        automation_session_id=session.id,
        round_number=1,
        started_by=aid[:256],
        trigger_type=AutomationRevisionRoundTrigger.INITIAL.value,
        instruction_text=None,
        target_scope=None,
        status="in_progress",
    )
    db.add(rnd)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_ROUND_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_prepare_plan",
        entity_type="automation_revision_round",
        entity_id=str(rnd.id),
        payload={"round_number": 1, "trigger": rnd.trigger_type},
    )
    db.flush()

    ctx = CodeSessionContext(db=db, session=session, job=job, actor_id=aid, revision_round=rnd)
    builder = AutomationEnginePayloadBuilder()
    req = builder.build_initial_request(session, job, rnd, actor_id=aid)

    try:
        adapter.run_plan_only_request(req, context=ctx)
    except FrameworkScanError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except ChangePlanRejected:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except EngineConfigurationError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except EngineAdapterError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise

    db.refresh(job)
    job.status = AutomationJobStatus.AWAITING_PLAN_APPROVAL.value
    session.plan_approved_at = None
    sync_session_status_from_job(session, job)
    db.flush()

    if isinstance(job.change_plan_json, dict) and job.change_plan_json:
        _record_round_artifacts(
            db,
            session=session,
            job=job,
            rnd=rnd,
            actor_id=aid,
            include_plan=True,
            include_patch=False,
            include_execution=False,
        )

    return session


def approve_automation_session_plan(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    job = db.get(AutomationJob, session.automation_job_id) if session.automation_job_id else None
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.AWAITING_PLAN_APPROVAL.value:
        raise ValueError("plan_not_ready")

    aid = actor_id.strip()
    if not aid:
        raise ValueError("actor_missing")

    rnd = _get_initial_round_in_progress(db, session.id)
    if rnd is None:
        raise ValueError("plan_round_missing")

    session.plan_approved_at = datetime.now(timezone.utc)
    rr = AutomationReviewRequest(
        automation_session_id=session.id,
        revision_round_id=rnd.id,
        actor_id=aid[:256],
        instruction_text="Plan approved.",
        target_scope=None,
        action_type=AutomationReviewRequestAction.APPROVE_PLAN.value,
        status=AutomationReviewRequestStatus.RECORDED.value,
    )
    db.add(rr)
    db.flush()
    sync_session_status_from_job(session, job)
    return session


def request_session_plan_revision(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str,
    instruction_text: str,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    job = db.get(AutomationJob, session.automation_job_id) if session.automation_job_id else None
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.AWAITING_PLAN_APPROVAL.value:
        raise ValueError("plan_not_ready")

    aid = actor_id.strip()
    inst = instruction_text.strip()
    if not aid:
        raise ValueError("actor_missing")
    if not inst:
        raise ValueError("instruction_missing")

    rnd = _get_initial_round_in_progress(db, session.id)
    if rnd is None:
        raise ValueError("plan_round_missing")

    session.plan_approved_at = None
    rr = AutomationReviewRequest(
        automation_session_id=session.id,
        revision_round_id=rnd.id,
        actor_id=aid[:256],
        instruction_text=inst[:20000],
        target_scope=None,
        action_type=AutomationReviewRequestAction.REQUEST_PLAN_REVISION.value,
        status=AutomationReviewRequestStatus.RECORDED.value,
    )
    db.add(rr)
    db.flush()

    job.status = AutomationJobStatus.PLANNING_CHANGES.value
    automation_job_service.plan_automation_job_changes(db, job.id, actor_id=aid)
    db.refresh(job)
    job.status = AutomationJobStatus.AWAITING_PLAN_APPROVAL.value
    sync_session_status_from_job(session, job)
    db.flush()

    if isinstance(job.change_plan_json, dict) and job.change_plan_json:
        _record_round_artifacts(
            db,
            session=session,
            job=job,
            rnd=rnd,
            actor_id=aid,
            include_plan=True,
            include_patch=False,
            include_execution=False,
        )
    return session


def _execute_automation_session_after_plan_approval(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str | None = None,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    if not session.automation_job_id:
        raise ValueError("session_missing_job")
    job = db.get(AutomationJob, session.automation_job_id)
    if job is None:
        raise ValueError("job_not_found")
    if job.status != AutomationJobStatus.AWAITING_PLAN_APPROVAL.value:
        raise ValueError("plan_not_ready")
    if session.plan_approved_at is None:
        raise ValueError("plan_not_approved")

    aid = (actor_id or session.created_by or "").strip()
    if not aid:
        raise ValueError("actor_missing")

    rnd = _get_initial_round_in_progress(db, session.id)
    if rnd is None:
        raise ValueError("plan_round_missing")

    job.status = AutomationJobStatus.GENERATING_CODE.value
    db.flush()

    adapter = resolve_coding_agent_adapter(session.coding_engine)
    ctx = CodeSessionContext(db=db, session=session, job=job, actor_id=aid, revision_round=rnd)
    builder = AutomationEnginePayloadBuilder()
    req = builder.build_initial_request(session, job, rnd, actor_id=aid)

    try:
        adapter.run_execute_after_plan_request(req, context=ctx)
    except PatchRejected:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except WorkspaceApplyRejected:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except EngineTimeoutError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except EngineRepoAccessError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except EngineMalformedOutputError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise
    except EngineAdapterError:
        _handle_initial_engine_errors(db, session=session, job=job, rnd=rnd)
        raise

    db.refresh(job)
    reconcile_job_for_session_approve(db, job)
    sync_session_status_from_job(session, job)
    db.flush()

    _record_round_artifacts(
        db,
        session=session,
        job=job,
        rnd=rnd,
        actor_id=aid,
        include_plan=False,
        include_patch=True,
        include_execution=True,
    )

    sync_session_status_from_job(session, job)
    rnd.status = "completed" if job.status != AutomationJobStatus.FAILED.value else "failed"
    session.current_round_number = 1
    db.flush()
    return session


def start_automation_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str | None = None,
    repository_connection_id: uuid.UUID | None = None,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    if not session.automation_job_id:
        raise ValueError("session_missing_job")
    job = db.get(AutomationJob, session.automation_job_id)
    if job is None:
        raise ValueError("job_not_found")

    if job.status == AutomationJobStatus.AWAITING_PLAN_APPROVAL.value:
        return _execute_automation_session_after_plan_approval(
            db, session_id, actor_id=actor_id
        )

    if session.current_round_number > 0:
        raise ValueError("session_already_started")
    if job.status != AutomationJobStatus.PENDING.value:
        raise ValueError("job_not_pending")

    aid = (actor_id or session.created_by or "").strip()
    if not aid:
        raise ValueError("actor_missing")

    _prepare_session_workspace_for_start(
        db,
        session=session,
        job=job,
        actor_id=aid,
        repository_connection_id=repository_connection_id,
    )

    adapter = resolve_coding_agent_adapter(session.coding_engine)
    rnd = AutomationRevisionRound(
        automation_session_id=session.id,
        round_number=1,
        started_by=aid[:256],
        trigger_type=AutomationRevisionRoundTrigger.INITIAL.value,
        instruction_text=None,
        target_scope=None,
        status="in_progress",
    )
    db.add(rnd)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_ROUND_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_start",
        entity_type="automation_revision_round",
        entity_id=str(rnd.id),
        payload={"round_number": 1, "trigger": rnd.trigger_type},
    )
    db.flush()

    ctx = CodeSessionContext(db=db, session=session, job=job, actor_id=aid, revision_round=rnd)
    builder = AutomationEnginePayloadBuilder()
    req = builder.build_initial_request(session, job, rnd, actor_id=aid)

    try:
        adapter.run_initial_request(req, context=ctx)
    except FrameworkScanError:
        rnd.status = "failed"
        session.current_round_number = 1
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except ChangePlanRejected:
        rnd.status = "failed"
        session.current_round_number = 1
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except EngineConfigurationError:
        rnd.status = "failed"
        session.current_round_number = 1
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except (PatchRejected, WorkspaceApplyRejected):
        rnd.status = "failed"
        session.current_round_number = 1
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except EngineTimeoutError:
        rnd.status = "failed"
        session.current_round_number = 1
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except EngineRepoAccessError:
        rnd.status = "failed"
        session.current_round_number = 1
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except EngineMalformedOutputError:
        rnd.status = "failed"
        session.current_round_number = 1
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except EngineAdapterError:
        rnd.status = "failed"
        session.current_round_number = 1
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise

    db.refresh(job)

    reconcile_job_for_session_approve(db, job)
    sync_session_status_from_job(session, job)
    db.flush()

    if isinstance(job.change_plan_json, dict) and job.change_plan_json:
        record_plan_version(
            db, session=session, revision_round=rnd, plan_json=dict(job.change_plan_json), created_by=aid
        )
    if isinstance(job.generated_patch_json, dict) and job.generated_patch_json:
        record_patch_version(
            db,
            session=session,
            revision_round=rnd,
            patch_json=_hydrate_patch_json_for_version_storage(job, dict(job.generated_patch_json)),
            created_by=aid,
        )
    if isinstance(job.execution_result_json, dict) and job.execution_result_json:
        record_execution_attempt(db, session=session, revision_round=rnd, job=job)

    sync_session_status_from_job(session, job)
    rnd.status = "completed" if job.status != AutomationJobStatus.FAILED.value else "failed"
    session.current_round_number = 1
    db.flush()

    return session



def request_session_revision(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str,
    instruction_text: str,
    target_scope: str | None,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    job = db.get(AutomationJob, session.automation_job_id) if session.automation_job_id else None
    if job is None:
        raise ValueError("job_not_found")

    next_n = session.current_round_number + 1
    rnd = AutomationRevisionRound(
        automation_session_id=session.id,
        round_number=next_n,
        started_by=actor_id.strip()[:256],
        trigger_type=AutomationRevisionRoundTrigger.REVIEW_REVISION.value,
        instruction_text=instruction_text.strip()[:20000],
        target_scope=(target_scope.strip()[:512] if target_scope else None),
        status="in_progress",
    )
    db.add(rnd)
    db.flush()

    rr = AutomationReviewRequest(
        automation_session_id=session.id,
        revision_round_id=rnd.id,
        actor_id=actor_id.strip()[:256],
        instruction_text=instruction_text.strip()[:20000],
        target_scope=(target_scope.strip()[:512] if target_scope else None),
        action_type=AutomationReviewRequestAction.REQUEST_REVISION.value,
        status=AutomationReviewRequestStatus.RECORDED.value,
    )
    db.add(rr)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_REQUEST_RECORDED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id.strip()[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_revision",
        entity_type="automation_review_request",
        entity_id=str(rr.id),
        payload={"action": rr.action_type},
    )
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_ROUND_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id.strip()[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_revision",
        entity_type="automation_revision_round",
        entity_id=str(rnd.id),
        payload={"round_number": next_n, "trigger": rnd.trigger_type},
    )
    db.flush()

    builder = AutomationEnginePayloadBuilder()
    req = builder.build_revision_request(
        session,
        job,
        rnd,
        actor_id=actor_id.strip(),
        instruction_text=instruction_text,
        target_scope=target_scope,
    )
    adapter = resolve_coding_agent_adapter(session.coding_engine)
    ctx = CodeSessionContext(
        db=db,
        session=session,
        job=job,
        actor_id=actor_id.strip(),
        revision_round=rnd,
    )
    workspace_root = resolve_revision_workspace_root(job, session.repo_path)
    scoped_paths = resolve_revision_scoped_paths(job, target_scope)
    try:
        _run_repo_bootstrap_for_session(
            db,
            session=session,
            job=job,
            actor_id=actor_id.strip(),
            workspace_profile=resolve_workspace_bootstrap_profile(
                session, job, prep=None, settings=get_settings()
            ),
            prep_mode=None,
        )
        before_snapshot = capture_workspace_snapshot(workspace_root, scoped_paths)
        adapter.run_revision_request(req, context=ctx)
        after_snapshot = capture_workspace_snapshot(workspace_root, scoped_paths)
        require_material_workspace_change(
            workspace_root,
            before=before_snapshot,
            after=after_snapshot,
        )
    except (
        RepoBootstrapError,
        HostedExecutionPreparationError,
        EngineConfigurationError,
        EngineTimeoutError,
        EngineRepoAccessError,
        EngineMalformedOutputError,
        EngineAdapterError,
    ):
        rr.status = AutomationReviewRequestStatus.FAILED.value
        rnd.status = "failed"
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except (PatchRejected, WorkspaceApplyRejected):
        rr.status = AutomationReviewRequestStatus.FAILED.value
        rnd.status = "failed"
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    except RevisionNoMaterialChangeError as e:
        rr.status = AutomationReviewRequestStatus.FAILED.value
        rnd.status = "failed"
        db.refresh(job)
        job.blocked_reason = e.message[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PATCH_VALIDATION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id.strip()[:256],
            workflow_run_id=session.workflow_run_id,
            step_name="automation_session_revision_material_change",
            entity_type="automation_revision_round",
            entity_id=str(rnd.id),
            payload={**e.result.to_audit_payload(), "code": e.code},
        )
        sync_session_status_from_job(session, job)
        db.flush()
        raise

    db.refresh(job)

    reconcile_job_for_session_approve(db, job)

    rr.status = AutomationReviewRequestStatus.APPLIED.value
    rnd.status = "completed" if job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value else "failed"
    session.current_round_number = next_n

    if isinstance(job.generated_patch_json, dict) and job.generated_patch_json:
        record_patch_version(
            db,
            session=session,
            revision_round=rnd,
            patch_json=_hydrate_patch_json_for_version_storage(job, dict(job.generated_patch_json)),
            created_by=actor_id.strip()[:256],
        )
    if isinstance(job.execution_result_json, dict) and job.execution_result_json:
        record_execution_attempt(db, session=session, revision_round=rnd, job=job)

    sync_session_status_from_job(session, job)
    db.flush()
    return session


def acknowledge_session_manual_edit(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str,
    note: str,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    job = db.get(AutomationJob, session.automation_job_id) if session.automation_job_id else None
    if job is None:
        raise ValueError("job_not_found")

    next_n = session.current_round_number + 1
    rnd = AutomationRevisionRound(
        automation_session_id=session.id,
        round_number=next_n,
        started_by=actor_id.strip()[:256],
        trigger_type=AutomationRevisionRoundTrigger.MANUAL_EDIT_RERUN.value,
        instruction_text=note.strip()[:5000],
        target_scope=None,
        status="in_progress",
    )
    db.add(rnd)
    db.flush()

    rr = AutomationReviewRequest(
        automation_session_id=session.id,
        revision_round_id=rnd.id,
        actor_id=actor_id.strip()[:256],
        instruction_text=note.strip()[:5000],
        target_scope=None,
        action_type=AutomationReviewRequestAction.MANUAL_EDIT_ACK.value,
        status=AutomationReviewRequestStatus.RECORDED.value,
    )
    db.add(rr)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_REQUEST_RECORDED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id.strip()[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_manual_ack",
        entity_type="automation_review_request",
        entity_id=str(rr.id),
        payload={"action": rr.action_type},
    )
    db.flush()

    builder = AutomationEnginePayloadBuilder()
    req = builder.build_manual_rerun_request(
        session, job, rnd, actor_id=actor_id.strip(), note=note.strip()
    )
    adapter = resolve_coding_agent_adapter(session.coding_engine)
    ctx = CodeSessionContext(
        db=db,
        session=session,
        job=job,
        actor_id=actor_id.strip(),
        revision_round=rnd,
    )
    try:
        _run_repo_bootstrap_for_session(
            db,
            session=session,
            job=job,
            actor_id=actor_id.strip(),
            workspace_profile=resolve_workspace_bootstrap_profile(
                session, job, prep=None, settings=get_settings()
            ),
            prep_mode=None,
        )
        adapter.run_manual_rerun_request(req, context=ctx)
    except (RepoBootstrapError, HostedExecutionPreparationError):
        rr.status = AutomationReviewRequestStatus.FAILED.value
        rnd.status = "failed"
        db.refresh(job)
        sync_session_status_from_job(session, job)
        db.flush()
        raise
    db.refresh(job)

    rr.status = AutomationReviewRequestStatus.APPLIED.value
    rnd.status = "completed" if job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value else "failed"
    session.current_round_number = next_n

    if isinstance(job.execution_result_json, dict) and job.execution_result_json:
        record_execution_attempt(db, session=session, revision_round=rnd, job=job)

    sync_session_status_from_job(session, job)
    db.flush()
    return session


def approve_automation_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str,
) -> AutomationSession:
    session = db.get(AutomationSession, session_id)
    if session is None:
        raise ValueError("session_not_found")
    job = db.get(AutomationJob, session.automation_job_id) if session.automation_job_id else None
    if job is None:
        raise ValueError("job_not_found")

    aid = actor_id.strip()
    if not aid:
        raise ValueError("review_actor_missing")

    reconcile_outcome = reconcile_job_for_session_approve(db, job)
    if reconcile_outcome == "already_approved":
        sync_session_status_from_job(session, job)
        db.flush()
        return session

    if job.status != AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value:
        summary = session_to_summary(db, session)
        try:
            from app.services.ui_v1_dashboard import map_backend_to_ui_dashboard_status

            summary = {**summary, "ui_status": map_backend_to_ui_dashboard_status(summary)}
        except Exception:
            pass
        raise ValueError(
            f"review_wrong_state|{build_session_approve_state_error_message(summary=summary, job=job)}"
        )

    rr = AutomationReviewRequest(
        automation_session_id=session.id,
        revision_round_id=None,
        actor_id=actor_id.strip()[:256],
        instruction_text=None,
        target_scope=None,
        action_type=AutomationReviewRequestAction.APPROVE.value,
        status=AutomationReviewRequestStatus.RECORDED.value,
    )
    db.add(rr)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REVIEW_REQUEST_RECORDED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id.strip()[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_approve",
        entity_type="automation_review_request",
        entity_id=str(rr.id),
        payload={"action": "approve"},
    )
    db.flush()

    automation_job_service.approve_automation_job_for_pr(db, job.id, actor_id=aid)
    db.refresh(job)

    rr.status = AutomationReviewRequestStatus.APPLIED.value
    sync_session_status_from_job(session, job)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_SESSION_APPROVED.value,
        actor_type=ActorType.USER.value,
        actor_id=actor_id.strip()[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_session_approve",
        entity_type="automation_session",
        entity_id=str(session.id),
        payload={"automation_job_id": str(job.id)},
    )
    db.flush()
    return session


def list_rounds_for_api(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AutomationRevisionRound)
            .where(AutomationRevisionRound.automation_session_id == session_id)
            .order_by(AutomationRevisionRound.round_number.asc())
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "round_number": r.round_number,
            "started_by": r.started_by,
            "trigger_type": r.trigger_type,
            "instruction_text": r.instruction_text,
            "target_scope": r.target_scope,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


def list_plan_versions_for_api(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AutomationPlanVersion)
            .where(AutomationPlanVersion.automation_session_id == session_id)
            .order_by(AutomationPlanVersion.version_number.asc())
        ).all()
    )
    return [
        {
            "id": str(p.id),
            "revision_round_id": str(p.revision_round_id),
            "version_number": p.version_number,
            "is_current": p.is_current,
            "created_by": p.created_by,
            "created_at": p.created_at.isoformat(),
            "plan_json": p.plan_json,
        }
        for p in rows
    ]


def list_patch_versions_for_api(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AutomationPatchVersion)
            .where(AutomationPatchVersion.automation_session_id == session_id)
            .order_by(AutomationPatchVersion.version_number.asc())
        ).all()
    )
    return [
        {
            "id": str(p.id),
            "revision_round_id": str(p.revision_round_id),
            "version_number": p.version_number,
            "is_current": p.is_current,
            "created_by": p.created_by,
            "created_at": p.created_at.isoformat(),
            "patch_json": p.patch_json,
        }
        for p in rows
    ]


def list_execution_attempts_for_api(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AutomationExecutionAttempt)
            .where(AutomationExecutionAttempt.automation_session_id == session_id)
            .order_by(AutomationExecutionAttempt.attempt_number.asc())
        ).all()
    )
    return [
        {
            "id": str(e.id),
            "revision_round_id": str(e.revision_round_id) if e.revision_round_id else None,
            "attempt_number": e.attempt_number,
            "target_test_file": e.target_test_file,
            "command_json": e.command_json,
            "result_json": e.result_json,
            "success": e.success,
            "created_at": e.created_at.isoformat(),
        }
        for e in rows
    ]


def list_review_requests_for_api(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(AutomationReviewRequest)
            .where(AutomationReviewRequest.automation_session_id == session_id)
            .order_by(AutomationReviewRequest.created_at.asc())
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "revision_round_id": str(r.revision_round_id) if r.revision_round_id else None,
            "actor_id": r.actor_id,
            "instruction_text": r.instruction_text,
            "target_scope": r.target_scope,
            "action_type": r.action_type,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
