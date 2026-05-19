"""GitHub source-control adapter — git subprocess + GitHub REST PR creation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.github_pr import GitHubApiError, create_pull_request
from app.core.config import Settings, get_settings
from app.core.constants import ActorType, AuditEventType
from app.db.models.automation_job import AutomationJob
from app.db.models.repository_connection import RepositoryConnection
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
    get_head_sha,
    git_author_from_settings,
    push_branch as git_push_branch,
    refresh_branch_from_base,
    stage_all_changes,
    working_tree_has_changes,
)
from app.services.pr_creation_service import _apply_pr_refresh_execution_outcome
from app.source_control.base_adapter import SourceControlProviderAdapterBase
from app.source_control.errors import (
    SourceControlAuthError,
    SourceControlConfigurationError,
    SourceControlCreateRequestError,
    SourceControlPushError,
    SourceControlRepoError,
)


def resolve_github_token(connection: RepositoryConnection, settings: Settings) -> str:
    """PAT from env var named in ``credential_reference`` or global ``GITHUB_TOKEN``."""
    ref = (connection.credential_reference or "").strip() or "GITHUB_TOKEN"
    if (connection.auth_type or "").strip() == "github_pat_env" or not connection.auth_type:
        v = (os.environ.get(ref) or settings.github_token or "").strip()
        if v:
            return v
    v = (settings.github_token or "").strip()
    if v:
        return v
    raise SourceControlAuthError(
        f"GitHub token missing (env {ref!r} or GITHUB_TOKEN / settings.github_token).",
        code="source_control_auth",
    )


class GitHubSourceControlAdapter(SourceControlProviderAdapterBase):
    """Git operations via :mod:`app.services.git_workspace_service`; PR via REST."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings

    def _s(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    @property
    def provider_name(self) -> str:
        return "github"

    def validate_config(self, connection: RepositoryConnection) -> bool:
        if not (connection.owner_or_org or "").strip() or not (connection.repo_name or "").strip():
            raise SourceControlConfigurationError(
                "GitHub connection requires owner_or_org and repo_name.",
                code="source_control_configuration",
            )
        resolve_github_token(connection, self._s())
        return True

    def get_default_branch(self, connection: RepositoryConnection) -> str:
        return (connection.default_branch or "main").strip() or "main"

    def ensure_working_branch(
        self,
        *,
        repo_root: Path,
        source_branch: str,
        target_branch: str,
    ) -> None:
        try:
            repo = ensure_git_repo(repo_root)
            ensure_branch(repo, source_branch, target_branch)
        except (GitWorkspaceError, FrameworkScanError) as e:
            msg = getattr(e, "message", str(e))
            raise SourceControlRepoError(msg, code="source_control_repo") from e

    def commit_workspace_changes(
        self,
        *,
        repo_root: Path,
        message: str,
    ) -> str:
        try:
            repo = ensure_git_repo(repo_root)
            ensure_git_author_identity(repo, settings=self._s())
            if not working_tree_has_changes(repo):
                raise GitWorkspaceError("nothing to commit: working tree clean")
            stage_all_changes(repo)
            create_commit(repo, message[:72])
            return get_head_sha(repo)
        except ValueError as e:
            if str(e) == "pr_git_author_not_configured":
                raise SourceControlConfigurationError(
                    "Set QSWARM_GIT_AUTHOR_NAME and QSWARM_GIT_AUTHOR_EMAIL (non-empty, valid email) "
                    "so QSwarm can configure repo-local git identity before commit.",
                    code="pr_git_author_not_configured",
                ) from e
            raise
        except GitWorkspaceError as e:
            raise SourceControlRepoError(e.message, code="source_control_repo") from e

    def push_branch(self, *, repo_root: Path, branch: str) -> None:
        try:
            repo = ensure_git_repo(repo_root)
            git_push_branch(repo, branch)
        except GitWorkspaceError as e:
            raise SourceControlPushError(e.message, code="source_control_push") from e

    def create_code_review_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        client: Any | None = None,
    ) -> dict[str, Any]:
        token = (self._s().github_token or "").strip()
        if not token:
            token = (os.environ.get("GITHUB_TOKEN") or "").strip()
        if not token:
            raise SourceControlAuthError("GitHub token missing for API call.", code="source_control_auth")
        try:
            return create_pull_request(
                token=token,
                owner=owner,
                repo=repo,
                title=title,
                body=body,
                head=head,
                base=base,
                api_base_url=self._s().github_api_base_url,
                client=client,
            )
        except GitHubApiError as e:
            raise SourceControlCreateRequestError(
                e.message,
                code="source_control_create_request",
            ) from e

    def run_session_pr_pipeline(
        self,
        db: Session,
        job: AutomationJob,
        *,
        repo_path: str,
        source_branch: str,
        target_branch: str,
        owner: str,
        repo_name: str,
        title: str,
        body: str,
        actor_id: str,
        token: str,
        subprocess_run: Any | None = None,
        pr_client: Any | None = None,
    ) -> dict[str, Any]:
        """
        Full refresh → commit → push → GitHub PR (mirrors job ``pr_creation_service`` flow).

        Returns dict with pr_number, pr_url, commit_sha, branch, refresh_notes, etc.
        """
        aid = actor_id.strip() or job.requested_by
        s = self._s()

        try:
            git_author_from_settings(s)
        except ValueError as e:
            if str(e) == "pr_git_author_not_configured":
                raise SourceControlConfigurationError(
                    "Set QSWARM_GIT_AUTHOR_NAME and QSWARM_GIT_AUTHOR_EMAIL (non-empty, valid email) "
                    "so QSwarm can configure repo-local git identity before commit.",
                    code="pr_git_author_not_configured",
                ) from e
            raise

        try:
            repo = ensure_git_repo(resolve_repo_path(repo_path))
        except (GitWorkspaceError, FrameworkScanError) as e:
            raise SourceControlRepoError(getattr(e, "message", str(e)), code="source_control_repo") from e

        try:
            ensure_branch(repo, source_branch, target_branch)
        except GitWorkspaceError as e:
            raise SourceControlRepoError(e.message, code="source_control_repo") from e

        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_BRANCH_CREATED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="session_pr_creation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"branch_name": source_branch},
        )
        db.flush()

        fetch_notes = fetch_base_branch(repo, target_branch)
        refresh = refresh_branch_from_base(repo, target_branch)
        refresh_notes: dict[str, Any] = {"fetch": fetch_notes, "merge": refresh}

        if refresh.get("conflicted"):
            abort_merge_if_in_progress(repo)
            audit_service.write_audit(
                db,
                event_type=AuditEventType.AUTOMATION_BASE_REFRESH_CONFLICT.value,
                actor_type=ActorType.SYSTEM.value,
                actor_id=aid,
                workflow_run_id=job.workflow_run_id,
                step_name="session_pr_creation",
                entity_type="automation_job",
                entity_id=str(job.id),
                payload={"conflict_files": (refresh.get("conflict_files") or [])[:30]},
            )
            db.flush()
            files = refresh.get("conflict_files") or []
            raise SourceControlRepoError(
                f"Merge conflict refreshing from {target_branch}; files: {', '.join(str(x) for x in files[:10])}",
                code="source_control_repo",
            )

        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_BASE_REFRESH_COMPLETED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="session_pr_creation",
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
                raise SourceControlRepoError(f"execution error: {e!s}", code="source_control_repo") from e
            if not _apply_pr_refresh_execution_outcome(db, job, rex, actor_id=aid):
                raise SourceControlRepoError(
                    "Playwright failed after base refresh before PR commit.",
                    code="source_control_repo",
                )

        try:
            if not working_tree_has_changes(repo):
                raise SourceControlRepoError(
                    "nothing to commit: working tree clean after refresh",
                    code="source_control_repo",
                )
            ensure_git_author_identity(repo, settings=s)
            stage_all_changes(repo)
            msg = f"test: automate {job.approved_case_id}"[:72]
            create_commit(repo, msg)
        except GitWorkspaceError as e:
            raise SourceControlRepoError(e.message, code="source_control_repo") from e

        try:
            sha = get_head_sha(repo)
        except GitWorkspaceError as e:
            raise SourceControlRepoError(e.message, code="source_control_repo") from e

        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_COMMIT_CREATED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="session_pr_creation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"commit_sha": sha},
        )
        db.flush()

        try:
            git_push_branch(repo, source_branch)
        except GitWorkspaceError as e:
            raise SourceControlPushError(e.message, code="source_control_push") from e

        try:
            gh = create_pull_request(
                token=token,
                owner=owner.strip(),
                repo=repo_name.strip(),
                title=title,
                body=body,
                head=source_branch,
                base=target_branch,
                api_base_url=s.github_api_base_url,
                client=pr_client,
            )
        except GitHubApiError as e:
            raise SourceControlCreateRequestError(
                f"GitHub API error: {e.message}",
                code="source_control_create_request",
            ) from e

        audit_service.write_audit(
            db,
            event_type=AuditEventType.AUTOMATION_PR_CREATED.value,
            actor_type=ActorType.SYSTEM.value,
            actor_id=aid,
            workflow_run_id=job.workflow_run_id,
            step_name="session_pr_creation",
            entity_type="automation_job",
            entity_id=str(job.id),
            payload={"pr_number": gh.get("number"), "pr_url": gh.get("html_url"), "branch": source_branch},
        )
        db.flush()

        return {
            "pr_number": gh.get("number"),
            "pr_url": gh.get("html_url"),
            "commit_sha": sha,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "refresh_notes": refresh_notes,
        }
