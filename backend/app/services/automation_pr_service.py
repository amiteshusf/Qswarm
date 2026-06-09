"""Session-scoped PR creation orchestration (provider-agnostic)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import (
    AuditEventType,
    AutomationJobStatus,
    ActorType,
    CodeReviewRequestStatus,
    SourceControlProviderName,
)
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_session import AutomationSession
from app.db.models.code_review_request import CodeReviewRequest
from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.db.models.repository_connection import RepositoryConnection
from app.services import audit_service, repository_connection_service
from app.services.automation_session_service import sync_session_status_from_job
from app.services.workspace_cache_service import ensure_pr_workspace_ready
from app.services.pr_template_render_service import (
    build_pr_template_context,
    validate_and_render_pr_template,
)
from app.services.pr_title_body_service import build_pr_title_and_body
from app.source_control.errors import (
    SourceControlConfigurationError,
    SourceControlProviderError,
    SourceControlPushError,
    SourceControlRepoError,
    UnsupportedSourceControlProviderError,
)
from app.source_control.github_provider_adapter import GitHubSourceControlAdapter, resolve_github_token
from app.source_control.registry import resolve_source_control_adapter


def render_source_branch(pattern: str, *, session_id: uuid.UUID, job: AutomationJob) -> str:
    aid = (job.approved_case_id or "case").replace("/", "-")[:64]
    return (
        (pattern or "qswarm/{session_id}")
        .replace("{session_id}", str(session_id))
        .replace("{job_id}", str(job.id))
        .replace("{approved_case_id}", aid)
    ).strip()[:500]


def _resolve_target_branch(
    *,
    session: AutomationSession,
    job: AutomationJob,
    policy: RepositoryBranchPolicy | None,
    connection: RepositoryConnection,
    override: str | None,
) -> str:
    if override and override.strip():
        return override.strip()[:256]
    if policy:
        return (policy.base_branch_default or connection.default_branch or session.base_branch or "main").strip()[
            :256
        ]
    return (connection.default_branch or session.base_branch or job.base_branch or "main").strip()[:256]


def _resolve_source_branch(
    *,
    session_id: uuid.UUID,
    job: AutomationJob,
    policy: RepositoryBranchPolicy | None,
    override: str | None,
) -> str:
    if override and override.strip():
        return override.strip()[:500]
    pat = policy.branch_naming_pattern if policy else "qswarm/{session_id}"
    return render_source_branch(pat, session_id=session_id, job=job)


def _resolve_title_body(
    session: AutomationSession,
    job: AutomationJob,
    policy: RepositoryBranchPolicy | None,
    connection: RepositoryConnection,
    title_override: str | None,
    body_override: str | None,
    *,
    source_branch: str,
    target_branch: str,
) -> tuple[str, str]:
    ctx = build_pr_template_context(
        session,
        job,
        repository_connection=connection,
        source_branch=source_branch,
        target_branch=target_branch,
    )
    if title_override and title_override.strip():
        title = title_override.strip()[:500]
    elif policy and policy.pr_title_template and policy.pr_title_template.strip():
        title = validate_and_render_pr_template(policy.pr_title_template.strip(), ctx)[:500]
    else:
        title, _ = build_pr_title_and_body(job)
    if body_override is not None and body_override.strip() != "":
        body = body_override[:65000]
    elif policy and policy.pr_body_template and policy.pr_body_template.strip():
        body = validate_and_render_pr_template(policy.pr_body_template.strip(), ctx)[:65000]
    else:
        _, body = build_pr_title_and_body(job)
    return title, body


def create_pr_for_automation_session(
    db: Session,
    session_id: uuid.UUID,
    *,
    actor_id: str,
    repository_connection_id: uuid.UUID,
    target_branch: str | None,
    source_branch: str | None,
    title_override: str | None,
    body_override: str | None,
    subprocess_run: Any | None = None,
    pr_client: Any | None = None,
) -> CodeReviewRequest:
    """
    ``approved_for_pr`` → ``creating_pr`` → ``pr_created`` (or ``pr_creation_failed``).

    Raises:
        ValueError: wrong state, missing prerequisites, duplicate PR.
        SourceControlProviderError: provider failures.
    """
    session = db.get(AutomationSession, session_id)
    if session is None or not session.automation_job_id:
        raise ValueError("session_not_found")
    job = db.get(AutomationJob, session.automation_job_id)
    if job is None:
        raise ValueError("job_not_found")

    if job.status == AutomationJobStatus.PR_CREATED.value:
        raise ValueError("pr_already_created")
    if job.status not in (
        AutomationJobStatus.APPROVED_FOR_PR.value,
        AutomationJobStatus.PR_CREATION_FAILED.value,
    ):
        raise ValueError("pr_wrong_state")

    conn = repository_connection_service.connection_with_policy(db, repository_connection_id)
    if conn is None or not conn.is_active:
        raise ValueError("repository_connection_not_found")

    policy = conn.branch_policy
    if policy and not policy.allow_session_override and (target_branch or source_branch):
        if (target_branch or "").strip() or (source_branch or "").strip():
            raise ValueError("branch_override_not_allowed")

    adapter = resolve_source_control_adapter(conn.provider)
    adapter.validate_config(conn)

    tgt = _resolve_target_branch(
        session=session, job=job, policy=policy, connection=conn, override=target_branch
    )
    src = _resolve_source_branch(session_id=session.id, job=job, policy=policy, override=source_branch)
    title, body = _resolve_title_body(
        session,
        job,
        policy,
        conn,
        title_override,
        body_override,
        source_branch=src,
        target_branch=tgt,
    )

    row = CodeReviewRequest(
        automation_session_id=session.id,
        repository_connection_id=conn.id,
        provider=conn.provider,
        source_branch=src,
        target_branch=tgt,
        title=title,
        body=body,
        status=CodeReviewRequestStatus.PENDING_CREATION.value,
        created_by=actor_id.strip()[:256],
        metadata_json=None,
    )
    db.add(row)
    db.flush()

    aid = actor_id.strip()[:256]
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_PR_CREATION_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=session.workflow_run_id,
        step_name="session_pr_creation",
        entity_type="automation_session",
        entity_id=str(session.id),
        payload={"repository_connection_id": str(conn.id), "source_branch": src, "target_branch": tgt},
    )
    db.flush()

    job.status = AutomationJobStatus.CREATING_PR.value
    sync_session_status_from_job(session, job)
    db.flush()

    try:
        if conn.provider != SourceControlProviderName.GITHUB.value:
            raise SourceControlProviderError(
                f"Provider {conn.provider!r} is not implemented for session PR.",
                code="unsupported_source_control_provider",
            )
        gh = GitHubSourceControlAdapter(get_settings())
        token = resolve_github_token(conn, get_settings())
        repo_ready = ensure_pr_workspace_ready(
            db,
            session=session,
            job=job,
            repository_connection_id=repository_connection_id,
            settings=get_settings(),
        )
        meta = gh.run_session_pr_pipeline(
            db,
            job,
            repo_path=repo_ready,
            source_branch=src,
            target_branch=tgt,
            owner=conn.owner_or_org,
            repo_name=conn.repo_name,
            title=title,
            body=body,
            actor_id=aid,
            token=token,
            subprocess_run=subprocess_run,
            pr_client=pr_client,
        )
        row.status = CodeReviewRequestStatus.CREATED.value
        row.external_id = str(meta.get("pr_number")) if meta.get("pr_number") is not None else None
        row.external_url = (str(meta.get("pr_url")).strip()[:1024] if meta.get("pr_url") else None)
        row.metadata_json = {**(row.metadata_json or {}), "pipeline": meta}
        job.status = AutomationJobStatus.PR_CREATED.value
        job.branch_name = src
        job.blocked_reason = None
        sync_session_status_from_job(session, job)
        db.flush()
        return row
    except SourceControlProviderError as e:
        row.status = CodeReviewRequestStatus.FAILED.value
        row.metadata_json = {
            **(row.metadata_json or {}),
            "error_code": getattr(e, "code", "source_control_provider_error"),
            "error_message": e.message[:4000],
        }
        job.status = AutomationJobStatus.PR_CREATION_FAILED.value
        job.blocked_reason = e.message[:2048]
        sync_session_status_from_job(session, job)
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PR_CREATION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=session.workflow_run_id,
            step_name="session_pr_creation",
            entity_type="automation_session",
            entity_id=str(session.id),
            payload={"code": getattr(e, "code", None), "message": e.message[:500]},
        )
        db.flush()
        raise
    except Exception as e:
        row.status = CodeReviewRequestStatus.FAILED.value
        row.metadata_json = {
            **(row.metadata_json or {}),
            "error_code": "unexpected",
            "error_message": str(e)[:4000],
        }
        job.status = AutomationJobStatus.PR_CREATION_FAILED.value
        job.blocked_reason = str(e)[:2048]
        sync_session_status_from_job(session, job)
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PR_CREATION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=session.workflow_run_id,
            step_name="session_pr_creation",
            entity_type="automation_session",
            entity_id=str(session.id),
            payload={"message": str(e)[:500]},
        )
        db.flush()
        raise


def list_code_review_requests_for_api(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(CodeReviewRequest)
            .where(CodeReviewRequest.automation_session_id == session_id)
            .order_by(CodeReviewRequest.created_at.asc())
        ).all()
    )
    return [
        {
            "id": str(r.id),
            "repository_connection_id": str(r.repository_connection_id),
            "provider": r.provider,
            "source_branch": r.source_branch,
            "target_branch": r.target_branch,
            "title": r.title,
            "body": r.body,
            "external_id": r.external_id,
            "external_url": r.external_url,
            "status": r.status,
            "created_by": r.created_by,
            "metadata_json": r.metadata_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
