"""Hosted automation workspace: clone + checkout under a QSwarm-managed directory."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_connection import RepositoryConnection
from app.services.framework_scan_service import FrameworkScanError
from app.services.repository_connection_service import get_repository_connection
from app.source_control.github_provider_adapter import resolve_github_token
from app.source_control.errors import SourceControlAuthError

logger = logging.getLogger(__name__)


class RepoWorkspaceError(Exception):
    """Base for workspace materialization failures (no secrets in ``message``)."""

    def __init__(self, message: str, *, code: str):
        self.message = message
        self.code = code
        super().__init__(message)


class RepoWorkspacePreparationError(RepoWorkspaceError):
    """Inputs or filesystem state prevent preparing a workspace."""


class RepoAuthError(RepoWorkspaceError):
    """Missing or invalid credentials for a hosted clone."""


class RepoCloneError(RepoWorkspaceError):
    """``git clone`` failed."""


class RepoCheckoutError(RepoWorkspaceError):
    """Post-clone branch checkout failed."""


@dataclass(frozen=True)
class WorkspacePreparationResult:
    mode: str  # "existing_path" | "cloned_workspace"
    workspace_path: str
    clone_url_used: str | None
    provider: str | None
    target_branch: str
    source_reference: str | None
    notes: str | None = None


WorkspaceBootstrapProfile = Literal["hosted_materialized", "local_existing"]


def resolve_workspace_bootstrap_profile(
    session: AutomationSession,
    job: AutomationJob,
    *,
    prep: WorkspacePreparationResult | None,
    settings: Settings | None = None,
) -> WorkspaceBootstrapProfile:
    """
    Decide npm bootstrap strictness.

    ``hosted_materialized`` applies when the workspace was just cloned **or** the resolved
    ``repo_path`` is the managed session directory (never treat as a casual local skip).
    """
    s = settings or get_settings()
    if prep is not None and prep.mode == "cloned_workspace":
        return "hosted_materialized"
    rp_s = (job.repo_path or session.repo_path or "").strip()
    if not rp_s:
        return "local_existing"
    try:
        rp = Path(rp_s).resolve()
        managed = session_repo_workspace_path(session.id, s.qswarm_workspace_root).resolve()
        if rp == managed:
            return "hosted_materialized"
    except OSError:
        return "local_existing"
    return "local_existing"


def session_repo_workspace_path(session_id: uuid.UUID, workspace_root: str | Path) -> Path:
    """Deterministic managed path: ``<root>/sessions/<session_id>/repo``."""
    root = Path(str(workspace_root).rstrip("/"))
    return root / "sessions" / str(session_id) / "repo"


def _try_existing_workspace(repo_path: str | None) -> Path | None:
    if repo_path is None or not str(repo_path).strip():
        return None
    raw = str(repo_path).strip()
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return None
    if not p.exists() or not p.is_dir():
        return None
    return p


def _redact_url_for_display(url: str) -> str:
    """Strip userinfo from URL for API / metadata (never return embedded tokens)."""
    try:
        parts = urlsplit(url.strip())
        if parts.username or parts.password:
            host = parts.hostname or ""
            netloc = host + (f":{parts.port}" if parts.port else "")
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return "<unparseable_url>"
    return url.strip()


def _github_https_from_ssh(url: str) -> str | None:
    u = url.strip()
    if u.startswith("git@github.com:"):
        rest = u.removeprefix("git@github.com:")
        if rest.endswith(".git"):
            rest = rest[:-4]
        seg = rest.split("/", 1)
        if len(seg) == 2:
            return f"https://github.com/{seg[0]}/{seg[1]}.git"
    return None


def _derive_public_clone_url(
    *,
    connection: RepositoryConnection | None,
    owner: str | None,
    repo: str | None,
) -> tuple[str | None, str | None]:
    """
    Returns (public_https_or_ssh_url, provider_slug).

    ``public`` means suitable for ``clone_url_used`` in responses (no PAT).
    """
    if connection is not None:
        prov = (connection.provider or "").strip().lower() or None
        raw = (connection.clone_url or "").strip()
        if raw:
            if raw.startswith("git@github.com:"):
                https = _github_https_from_ssh(raw)
                return (https or raw, prov)
            return (_redact_url_for_display(raw), prov)
        own = (connection.owner_or_org or "").strip()
        rn = (connection.repo_name or "").strip()
        if prov == "github" and own and rn:
            return (f"https://github.com/{own}/{rn}.git", prov)
        return (None, prov)
    own = (owner or "").strip()
    rn = (repo or "").strip()
    if own and rn:
        return (f"https://github.com/{own}/{rn}.git", "github")
    return (None, None)


def _github_authenticated_https_url(https_url: str, token: str) -> str:
    parts = urlsplit(https_url.strip())
    if (parts.hostname or "").lower() != "github.com":
        return https_url
    host = parts.hostname or "github.com"
    netloc = f"x-access-token:{token}@{host}"
    if parts.port:
        netloc = f"x-access-token:{token}@{host}:{parts.port}"
    return urlunsplit((parts.scheme or "https", netloc, parts.path, parts.query, parts.fragment))


def _resolve_github_pat_for_clone(
    connection: RepositoryConnection | None,
    settings: Settings,
) -> str:
    if connection is not None:
        try:
            return resolve_github_token(connection, settings)
        except SourceControlAuthError as e:
            raise RepoAuthError(
                "GitHub token is required for hosted clone (set GITHUB_TOKEN or connection credential env).",
                code="repo_auth_required",
            ) from e
    tok = (settings.github_token or "").strip()
    if not tok:
        raise RepoAuthError(
            "GitHub token is required for hosted clone when no RepositoryConnection supplies credentials "
            "(set GITHUB_TOKEN).",
            code="repo_auth_required",
        )
    return tok


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _run_git(
    cwd: Path | None,
    args: list[str],
    *,
    timeout: int,
    git_bin: str = "git",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git_bin, *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_git_env(),
    )


def _sanitize_git_stderr(msg: str) -> str:
    """Best-effort redaction of accidental token echoes in git output."""
    if not msg:
        return ""
    redacted = re.sub(
        r"(https?://)([^@\s]+)(@github\.com)",
        r"\1***\3",
        msg,
        flags=re.IGNORECASE,
    )
    return redacted.strip()[:2000]


def _checkout_target_branch(repo: Path, branch: str, *, fetch_timeout: int, checkout_timeout: int) -> None:
    br = (branch or "main").strip() or "main"
    fe = _run_git(repo, ["fetch", "origin", br], timeout=fetch_timeout)
    if fe.returncode != 0:
        fe2 = _run_git(repo, ["fetch", "origin"], timeout=fetch_timeout)
        if fe2.returncode != 0:
            raise RepoCheckoutError(
                f"git fetch failed before checkout: {_sanitize_git_stderr(fe2.stderr or fe2.stdout)}",
                code="repo_checkout_failed",
            )
    rem = _run_git(repo, ["rev-parse", "--verify", f"refs/remotes/origin/{br}"], timeout=30)
    if rem.returncode == 0:
        co = _run_git(repo, ["checkout", "-B", br, f"origin/{br}"], timeout=checkout_timeout)
    else:
        co = _run_git(repo, ["checkout", br], timeout=checkout_timeout)
    if co.returncode != 0:
        raise RepoCheckoutError(
            f"git checkout failed for branch {br!r}: {_sanitize_git_stderr(co.stderr or co.stdout)}",
            code="repo_checkout_failed",
        )


def _clone_fresh(repo_root: Path, clone_argv: list[str], *, timeout: int) -> None:
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    if repo_root.exists():
        for x in repo_root.iterdir():
            raise RepoWorkspacePreparationError(
                f"Workspace path {repo_root} exists and is not empty (non-git); remove or pick another session.",
                code="repo_workspace_obstructed",
            )
    r = subprocess.run(
        clone_argv,
        cwd=str(repo_root.parent),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_git_env(),
    )
    if r.returncode != 0:
        raise RepoCloneError(
            f"git clone failed: {_sanitize_git_stderr(r.stderr or r.stdout)}",
            code="repo_clone_failed",
        )


def _refresh_existing_git_workspace(
    repo_root: Path,
    authenticated_url: str,
    *,
    fetch_timeout: int,
    checkout_timeout: int,
    branch: str,
) -> None:
    """Fetch + checkout when ``repo`` already exists as a git work tree."""
    fe = _run_git(repo_root, ["fetch", "origin"], timeout=fetch_timeout)
    if fe.returncode != 0:
        raise RepoCloneError(
            f"git fetch failed in existing workspace: {_sanitize_git_stderr(fe.stderr or fe.stdout)}",
            code="repo_clone_failed",
        )
    _checkout_target_branch(repo_root, branch, fetch_timeout=fetch_timeout, checkout_timeout=checkout_timeout)
    _run_git(repo_root, ["remote", "set-url", "origin", authenticated_url], timeout=30)


def _clone_or_refresh(
    repo_root: Path,
    *,
    authenticated_url: str,
    branch: str,
    settings: Settings,
) -> None:
    clone_timeout = int(settings.qswarm_git_clone_timeout_seconds)
    fetch_timeout = int(settings.qswarm_git_fetch_timeout_seconds)
    checkout_timeout = int(settings.qswarm_git_fetch_timeout_seconds)

    if (repo_root / ".git").exists():
        _refresh_existing_git_workspace(
            repo_root,
            authenticated_url,
            fetch_timeout=fetch_timeout,
            checkout_timeout=checkout_timeout,
            branch=branch,
        )
        return

    if repo_root.exists() and any(repo_root.iterdir()):
        raise RepoWorkspacePreparationError(
            f"Workspace path {repo_root} exists but is not a git repository.",
            code="repo_workspace_obstructed",
        )

    argv = ["git", "clone", "--no-single-branch", authenticated_url, str(repo_root)]
    _clone_fresh(repo_root, argv, timeout=clone_timeout)
    _checkout_target_branch(repo_root, branch, fetch_timeout=fetch_timeout, checkout_timeout=checkout_timeout)
    _run_git(repo_root, ["remote", "set-url", "origin", authenticated_url], timeout=30)


def _resolve_target_branch(
    *,
    session: AutomationSession,
    connection: RepositoryConnection | None,
) -> str:
    sb = (session.base_branch or "").strip()
    if sb:
        return sb
    if connection is not None:
        default_br = (connection.default_branch or "").strip()
        if default_br:
            return default_br
    return "main"


def prepare_automation_session_workspace(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    repository_connection_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> WorkspacePreparationResult:
    """
    Ensure ``job.repo_path`` / ``session.repo_path`` resolve to a usable workspace.

    - If a declared local path exists, use it (no clone).
    - Otherwise clone using repository connection and/or owner+repo metadata.
    """
    s = settings or get_settings()
    declared = (job.repo_path or session.repo_path or "").strip() or None
    existing = _try_existing_workspace(declared)
    if existing is not None:
        resolved = str(existing)
        job.repo_path = resolved
        session.repo_path = resolved
        db.flush()
        return WorkspacePreparationResult(
            mode="existing_path",
            workspace_path=resolved,
            clone_url_used=None,
            provider=None,
            target_branch=(session.base_branch or "main").strip() or "main",
            source_reference=session.source_reference,
            notes="Using existing local repo_path",
        )

    conn_id = repository_connection_id or session.repository_connection_id
    connection: RepositoryConnection | None = None
    if conn_id is not None:
        connection = get_repository_connection(db, conn_id)
        if connection is None:
            raise RepoWorkspacePreparationError(
                "repository_connection_id does not reference an existing connection.",
                code="repository_connection_not_found",
            )
        if not connection.is_active:
            raise RepoWorkspacePreparationError(
                "repository_connection is inactive.",
                code="repository_connection_inactive",
            )

    def _nz(v: str | None) -> str | None:
        t = (v or "").strip()
        return t or None

    owner = _nz(job.repo_owner) or _nz(session.repo_owner) or (_nz(connection.owner_or_org) if connection else None)
    repo = _nz(job.repo_name) or _nz(session.repo_name) or (_nz(connection.repo_name) if connection else None)

    public_url, provider = _derive_public_clone_url(connection=connection, owner=owner, repo=repo)
    if not public_url:
        if declared:
            raise FrameworkScanError(
                "repo_path_not_found",
                f"repo_path does not exist: {declared}",
            )
        raise RepoWorkspacePreparationError(
            "Cannot materialize workspace: set repo_path to an existing directory, "
            "or provide repository_connection_id / repo_owner+repo_name for GitHub clone.",
            code="repo_clone_source_unresolvable",
        )

    if (provider or "").lower() not in ("", "github"):
        raise RepoWorkspacePreparationError(
            f"Hosted materialization for provider {provider!r} is not implemented (GitHub only in this release).",
            code="repo_clone_source_unresolvable",
        )

    if not public_url.lower().startswith("https://github.com"):
        raise RepoWorkspacePreparationError(
            "Hosted materialization currently supports github.com HTTPS URLs only.",
            code="repo_clone_source_unresolvable",
        )

    token = _resolve_github_pat_for_clone(connection, s)
    auth_url = _github_authenticated_https_url(public_url, token)

    repo_root = session_repo_workspace_path(session.id, s.qswarm_workspace_root)
    branch = _resolve_target_branch(session=session, connection=connection)

    try:
        repo_root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RepoWorkspacePreparationError(
            f"Cannot create workspace directories: {e}",
            code="repo_workspace_mkdir_failed",
        ) from e

    try:
        _clone_or_refresh(
            repo_root,
            authenticated_url=auth_url,
            branch=branch,
            settings=s,
        )
    except (RepoCloneError, RepoCheckoutError):
        raise
    except RepoWorkspacePreparationError:
        raise
    except subprocess.TimeoutExpired as e:
        raise RepoCloneError("git operation timed out", code="repo_clone_failed") from e

    resolved = str(repo_root.resolve())
    job.repo_path = resolved
    session.repo_path = resolved
    db.flush()

    logger.info(
        "automation_workspace_prepared",
        extra={
            "mode": "cloned_workspace",
            "session_id": str(session.id),
            "workspace_path": resolved,
            "provider": provider or "github",
            "target_branch": branch,
        },
    )

    return WorkspacePreparationResult(
        mode="cloned_workspace",
        workspace_path=resolved,
        clone_url_used=_redact_url_for_display(public_url),
        provider=provider or "github",
        target_branch=branch,
        source_reference=session.source_reference,
        notes="Repository cloned or refreshed under QSwarm-managed path",
    )


def preparation_result_to_audit_payload(result: WorkspacePreparationResult) -> dict[str, Any]:
    """Structured audit payload without secrets."""
    return {
        "workspace_mode": result.mode,
        "workspace_path": result.workspace_path,
        "clone_url_used": result.clone_url_used,
        "provider": result.provider,
        "target_branch": result.target_branch,
        "source_reference": result.source_reference,
        "notes": result.notes,
    }
