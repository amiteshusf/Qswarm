"""Pre-PR base refresh, commit, push, and GitHub PR creation for approved automation jobs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.connectors.github_pr import GitHubApiError, create_pull_request
from app.core.config import get_settings
from app.core.constants import ActorType, AuditEventType, AutomationJobStatus, PrRecordStatus
from app.db.models.automation_job import AutomationJob
from app.db.models.pr_record import PrRecord
from app.services import audit_service
from app.services.execution_service import run_playwright_execution_for_job
from app.services.failure_analysis_service import analyze_execution_failure
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path
from app.services.git_workspace_service import (
    GitWorkspaceError,
    abort_merge_if_in_progress,
    create_commit,
    ensure_branch,
    ensure_git_author_identity,
    ensure_git_repo,
    fetch_base_branch,
    git_author_from_settings,
    get_head_sha,
    job_branch_name,
    push_branch,
    refresh_branch_from_base,
    stage_all_changes,
    working_tree_has_changes,
)
from app.services.pr_title_body_service import build_pr_title_and_body


def _resolve_github_repo(
    job: AutomationJob,
    *,
    repo_owner: str | None,
    repo_name: str | None,
) -> tuple[str, str]:
    s = get_settings()
    own = (repo_owner or job.repo_owner or s.github_default_repo_owner or "").strip()
    rep = (repo_name or job.repo_name or s.github_default_repo_name or "").strip()
    if not own or not rep:
        raise ValueError("pr_prerequisites_missing")
    return own, rep


def _apply_pr_refresh_execution_outcome(
    db: Session,
    job: AutomationJob,
    rex: dict[str, Any],
    *,
    actor_id: str,
) -> bool:
    """Persist post-refresh execution; return True if tests passed. May set job to failed/human."""
    aid = actor_id.strip() or job.requested_by
    job.execution_result_json = {**rex, "after_pr_base_refresh": True}
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_REEXECUTION_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "success": bool(rex.get("success")),
            "exit_code": rex.get("exit_code"),
            "context": "pr_base_refresh",
        },
    )
    if rex.get("success"):
        job.failure_analysis_json = None
        db.flush()
        return True

    fa = analyze_execution_failure(rex)
    job.failure_analysis_json = fa
    if fa.get("needs_human_input"):
        job.status = AutomationJobStatus.AWAITING_HUMAN_INPUT.value
        job.blocked_reason = (
            str(fa.get("clarification_question") or fa.get("root_cause_summary") or "Human input required")
        )[:2048]
    else:
        job.status = AutomationJobStatus.FAILED.value
        notes = rex.get("notes") or []
        job.blocked_reason = (str(notes[0]) if notes else "Execution failed after base refresh")[:2048]
    db.flush()
    return False


def create_pull_request_for_job(
    db: Session,
    job: AutomationJob,
    *,
    actor_id: str,
    repo_owner: str | None = None,
    repo_name: str | None = None,
    subprocess_run: Any | None = None,
) -> PrRecord:
    """
    Full PR pipeline for ``approved_for_pr`` jobs.

    Raises:
        ValueError: ``pr_wrong_state``, ``pr_prerequisites_missing``, ``pr_git_author_not_configured``.
    """
    if job.status != AutomationJobStatus.APPROVED_FOR_PR.value:
        raise ValueError("pr_wrong_state")
    if not job.repo_path or not str(job.repo_path).strip():
        raise ValueError("pr_prerequisites_missing")

    s = get_settings()
    token = (s.github_token or "").strip()
    if not token:
        raise ValueError("pr_prerequisites_missing")
    git_author_from_settings(s)

    own, rep = _resolve_github_repo(job, repo_owner=repo_owner, repo_name=repo_name)

    aid = actor_id.strip() or job.requested_by
    pr_title, pr_body = build_pr_title_and_body(job)
    branch = (job.branch_name or "").strip() or job_branch_name(job.approved_case_id, job.id)

    pr_row = PrRecord(
        automation_job_id=job.id,
        provider="github",
        repo_owner=own,
        repo_name=rep,
        base_branch=job.base_branch or "main",
        branch_name=branch,
        commit_sha=None,
        pr_number=None,
        pr_url=None,
        status=PrRecordStatus.BRANCH_READY.value,
        title=pr_title,
        body=pr_body,
        refresh_status=None,
        refresh_notes_json=None,
    )
    db.add(pr_row)
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_PR_CREATION_STARTED.value,
        actor_type=ActorType.USER.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"branch": branch, "base_branch": job.base_branch},
    )
    db.flush()

    def _fail_pr(msg: str, *, job_status: str = AutomationJobStatus.FAILED.value) -> None:
        pr_row.status = PrRecordStatus.FAILED.value
        pr_row.refresh_notes_json = {**(pr_row.refresh_notes_json or {}), "error": msg[:2000]}
        job.status = job_status
        job.blocked_reason = msg[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PR_CREATION_FAILED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="pr_creation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"message": msg[:500]},
        )
        db.flush()

    try:
        repo = ensure_git_repo(resolve_repo_path(job.repo_path))
    except (GitWorkspaceError, FrameworkScanError) as e:
        msg = getattr(e, "message", str(e))
        _fail_pr(str(msg))
        return pr_row

    try:
        ensure_branch(repo, branch, job.base_branch or "main")
    except GitWorkspaceError as e:
        _fail_pr(e.message)
        return pr_row

    job.branch_name = branch
    pr_row.branch_name = branch
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_BRANCH_CREATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"branch_name": branch},
    )
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_BASE_REFRESH_STARTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"base_branch": job.base_branch},
    )
    db.flush()

    fetch_notes = fetch_base_branch(repo, job.base_branch or "main")
    refresh = refresh_branch_from_base(repo, job.base_branch or "main")
    pr_row.refresh_notes_json = {"fetch": fetch_notes, "merge": refresh}
    pr_row.refresh_status = "conflict" if refresh.get("conflicted") else "ok"
    db.flush()

    if refresh.get("conflicted"):
        abort_merge_if_in_progress(repo)
        pr_row.status = PrRecordStatus.BASE_REFRESH_CONFLICT.value
        job.status = AutomationJobStatus.AWAITING_HUMAN_INPUT.value
        files = refresh.get("conflict_files") or []
        job.blocked_reason = (
            f"Merge conflict refreshing from {job.base_branch}; files: {', '.join(files[:10])}"
        )[:2048]
        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_BASE_REFRESH_CONFLICT.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="pr_creation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"conflict_files": files[:30]},
        )
        db.flush()
        return pr_row

    pr_row.status = PrRecordStatus.BASE_REFRESHED.value
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_BASE_REFRESH_COMPLETED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"updated": bool(refresh.get("updated"))},
    )
    db.flush()

    ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
    need_rerun = bool(refresh.get("updated")) or ex.get("success") is not True
    if need_rerun:
        try:
            rex = run_playwright_execution_for_job(job, subprocess_run=subprocess_run)
        except Exception as e:  # pragma: no cover
            _fail_pr(f"execution error: {e!s}")
            return pr_row
        if not _apply_pr_refresh_execution_outcome(db, job, rex, actor_id=aid):
            pr_row.status = PrRecordStatus.FAILED.value
            audit_service.write_audit(
                db,
                event_type=AuditEventType.AUTOMATION_PR_CREATION_FAILED.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=aid,
                workflow_run_id=job.workflow_run_id,
                step_name="pr_creation",
                entity_type="automation_job",
                entity_id=str(job.id),
                payload={"stage": "post_refresh_execution"},
            )
            db.flush()
            return pr_row

    try:
        if not working_tree_has_changes(repo):
            _fail_pr("nothing to commit: working tree clean after refresh")
            return pr_row
        ensure_git_author_identity(repo, settings=s)
        stage_all_changes(repo)
        create_commit(repo, f"test: automate {job.approved_case_id}"[:72])
    except GitWorkspaceError as e:
        _fail_pr(e.message)
        return pr_row

    try:
        sha = get_head_sha(repo)
    except GitWorkspaceError as e:
        _fail_pr(e.message)
        return pr_row

    pr_row.commit_sha = sha
    pr_row.status = PrRecordStatus.COMMITTED.value
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_COMMIT_CREATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"commit_sha": sha},
    )
    db.flush()

    try:
        push_branch(repo, branch)
    except GitWorkspaceError as e:
        _fail_pr(e.message)
        return pr_row

    try:
        gh = create_pull_request(
            token=token,
            owner=own,
            repo=rep,
            title=pr_title,
            body=pr_body,
            head=branch,
            base=job.base_branch or "main",
            api_base_url=s.github_api_base_url,
        )
    except GitHubApiError as e:
        _fail_pr(f"GitHub API error: {e.message}")
        return pr_row

    pr_row.pr_number = gh.get("number")
    pr_row.pr_url = gh.get("html_url")
    pr_row.status = PrRecordStatus.PR_CREATED.value
    job.status = AutomationJobStatus.PR_CREATED.value
    job.blocked_reason = None
    db.flush()

    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_PR_CREATED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=aid,
        workflow_run_id=job.workflow_run_id,
        step_name="pr_creation",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={
            "pr_number": pr_row.pr_number,
            "pr_url": pr_row.pr_url,
            "branch_name": branch,
        },
    )
    db.flush()
    return pr_row
