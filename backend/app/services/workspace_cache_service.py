"""Short-lived workspace cache and durable PR workspace preparation for hosted sessions."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_patch_version import AutomationPatchVersion
from app.db.models.automation_session import AutomationSession
from app.db.models.workspace_cache_entry import WorkspaceCacheEntry
from app.services.git_workspace_service import GitWorkspaceError, ensure_git_repo, working_tree_has_changes
from app.services.repo_workspace_service import (
    RepoWorkspaceError,
    WorkspacePreparationResult,
    prepare_automation_session_workspace,
)
from app.services.workspace_service import WorkspaceApplyError, reapply_patch_for_pr_commit
from app.source_control.errors import SourceControlConfigurationError, SourceControlRepoError

logger = logging.getLogger(__name__)

WORKSPACE_CACHE_STATUS_ACTIVE = "active"
WORKSPACE_CACHE_STATUS_IDLE = "idle"
WORKSPACE_CACHE_STATUS_EXPIRED = "expired"
WORKSPACE_CACHE_STATUS_DELETED = "deleted"


@dataclass(frozen=True)
class PrWorkspaceReady:
    """Repo root plus current patch snapshot used for session create-pr."""

    repo_path: str
    patch_files: list[dict[str, Any]]
    patch_version_id: uuid.UUID
    patch_version_number: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_delta(settings: Settings) -> timedelta:
    return timedelta(minutes=int(settings.qswarm_workspace_cache_ttl_minutes))


def workspace_exists(path: str | Path | None) -> bool:
    if path is None:
        return False
    raw = str(path).strip()
    if not raw:
        return False
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return False
    return p.is_dir()


def workspace_path_usable(path: str | Path | None) -> bool:
    if not workspace_exists(path):
        return False
    try:
        ensure_git_repo(Path(str(path).strip()))
        return True
    except GitWorkspaceError:
        return False


def expire_due_workspace_cache_entries(db: Session, *, now: datetime | None = None) -> int:
    """Mark cache rows past ``expires_at`` as expired (DB-only). Returns rows updated."""
    t = now or _utcnow()
    res = db.execute(
        update(WorkspaceCacheEntry)
        .where(
            WorkspaceCacheEntry.expires_at < t,
            WorkspaceCacheEntry.status.in_(
                (WORKSPACE_CACHE_STATUS_ACTIVE, WORKSPACE_CACHE_STATUS_IDLE)
            ),
        )
        .values(status=WORKSPACE_CACHE_STATUS_EXPIRED, updated_at=t)
    )
    db.flush()
    return int(res.rowcount or 0)


def _deactivate_session_active_rows(db: Session, session_id: uuid.UUID, *, now: datetime) -> None:
    db.execute(
        update(WorkspaceCacheEntry)
        .where(
            WorkspaceCacheEntry.automation_session_id == session_id,
            WorkspaceCacheEntry.status == WORKSPACE_CACHE_STATUS_ACTIVE,
        )
        .values(status=WORKSPACE_CACHE_STATUS_EXPIRED, updated_at=now)
    )


def get_active_workspace_for_session(db: Session, session_id: uuid.UUID) -> WorkspaceCacheEntry | None:
    return db.scalar(
        select(WorkspaceCacheEntry).where(
            WorkspaceCacheEntry.automation_session_id == session_id,
            WorkspaceCacheEntry.status == WORKSPACE_CACHE_STATUS_ACTIVE,
        )
    )


def _upsert_active_cache(
    db: Session,
    *,
    session: AutomationSession,
    workspace_path: str,
    repository_connection_id: uuid.UUID | None,
    settings: Settings,
) -> WorkspaceCacheEntry:
    now = _utcnow()
    expire_due_workspace_cache_entries(db, now=now)
    _deactivate_session_active_rows(db, session.id, now=now)
    ttl = _ttl_delta(settings)
    row = WorkspaceCacheEntry(
        automation_session_id=session.id,
        repository_connection_id=repository_connection_id,
        workspace_path=workspace_path,
        status=WORKSPACE_CACHE_STATUS_ACTIVE,
        last_used_at=now,
        expires_at=now + ttl,
    )
    db.add(row)
    db.flush()
    return row


def mark_workspace_used(
    db: Session,
    *,
    session_id: uuid.UUID,
    workspace_path: str,
    repository_connection_id: uuid.UUID | None,
    settings: Settings | None = None,
) -> None:
    s = settings or get_settings()
    now = _utcnow()
    ttl = _ttl_delta(s)
    row = db.scalar(
        select(WorkspaceCacheEntry).where(
            WorkspaceCacheEntry.automation_session_id == session_id,
            WorkspaceCacheEntry.workspace_path == workspace_path,
            WorkspaceCacheEntry.status == WORKSPACE_CACHE_STATUS_ACTIVE,
        )
    )
    if row is None:
        sess = db.get(AutomationSession, session_id)
        if sess is None:
            return
        _upsert_active_cache(
            db,
            session=sess,
            workspace_path=workspace_path,
            repository_connection_id=repository_connection_id,
            settings=s,
        )
        return
    row.last_used_at = now
    row.expires_at = now + ttl
    if repository_connection_id is not None:
        row.repository_connection_id = repository_connection_id
    row.updated_at = now
    db.flush()


def expire_workspace(db: Session, *, entry_id: uuid.UUID) -> bool:
    row = db.get(WorkspaceCacheEntry, entry_id)
    if row is None:
        return False
    row.status = WORKSPACE_CACHE_STATUS_EXPIRED
    row.updated_at = _utcnow()
    db.flush()
    return True


def create_workspace_for_session(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    repository_connection_id: uuid.UUID,
    settings: Settings | None = None,
) -> WorkspacePreparationResult:
    """Materialize managed clone (caller clears invalid ``repo_path`` first when needed)."""
    s = settings or get_settings()
    return prepare_automation_session_workspace(
        db,
        session=session,
        job=job,
        repository_connection_id=repository_connection_id,
        settings=s,
    )


def record_workspace_cache_after_hosted_materialize(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    prep: WorkspacePreparationResult,
    repository_connection_id: uuid.UUID | None,
    settings: Settings | None = None,
) -> None:
    """After session start clone: track one active cache row (hosted materialization only)."""
    if prep.mode != "cloned_workspace":
        return
    s = settings or get_settings()
    path = (job.repo_path or session.repo_path or prep.workspace_path or "").strip()
    if not path:
        return
    cid = repository_connection_id or session.repository_connection_id
    _upsert_active_cache(db, session=session, workspace_path=path, repository_connection_id=cid, settings=s)
    logger.info(
        "workspace_cache_recorded",
        extra={"session_id": str(session.id), "workspace_path": path},
    )


def _current_patch_version(db: Session, session_id: uuid.UUID) -> AutomationPatchVersion | None:
    return db.scalar(
        select(AutomationPatchVersion).where(
            AutomationPatchVersion.automation_session_id == session_id,
            AutomationPatchVersion.is_current.is_(True),
        )
    )


def load_current_patch_files(db: Session, session_id: uuid.UUID) -> tuple[AutomationPatchVersion, list[dict[str, Any]]]:
    """Return the current ``AutomationPatchVersion`` row and normalized generated_files."""
    patch_v = _current_patch_version(db, session_id)
    if patch_v is None:
        raise SourceControlConfigurationError(
            "No current patch version for this session; cannot create PR.",
            code="pr_no_current_patch",
        )
    files = _extract_generated_files(dict(patch_v.patch_json or {}))
    if not files:
        raise SourceControlConfigurationError(
            "Current patch version has no generated_files with content; cannot create PR.",
            code="pr_no_current_patch",
        )
    return patch_v, files


def reapply_current_patch_for_pr_commit(
    repo_root: Path,
    patch_files: list[dict[str, Any]],
    *,
    patch_version_id: uuid.UUID,
    patch_version_number: int,
    target_branch: str,
) -> dict[str, Any]:
    """
    Write the approved patch onto the checked-out branch and require a non-clean tree.

    Raises:
        SourceControlRepoError: apply failed or patch content matches base with no diff.
    """
    if not patch_files:
        raise SourceControlRepoError(
            "Current patch version has no files to re-apply before commit.",
            code="source_control_repo",
        )
    try:
        result = reapply_patch_for_pr_commit(repo_root, patch_files)
    except WorkspaceApplyError as e:
        raise SourceControlRepoError(
            f"Failed to re-apply current patch version {patch_version_number} before commit: {e.message}",
            code="source_control_repo",
        ) from e

    repo = ensure_git_repo(repo_root)
    if working_tree_has_changes(repo):
        return {
            **result,
            "patch_version_id": str(patch_version_id),
            "patch_version_number": patch_version_number,
            "has_working_tree_diff": True,
        }

    paths = ", ".join(str(x.get("path")) for x in patch_files[:8])
    extra = f" (+{len(patch_files) - 8} more)" if len(patch_files) > 8 else ""
    raise SourceControlRepoError(
        "nothing to commit after re-applying current patch version "
        f"{patch_version_number} (id={patch_version_id}) onto branch refreshed from "
        f"{target_branch!r}; {len(patch_files)} file(s) ({paths}{extra}) produced no net git diff",
        code="source_control_repo",
    )


def _extract_generated_files(patch_json: dict[str, Any]) -> list[dict[str, Any]]:
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


def ensure_pr_workspace_ready(
    db: Session,
    *,
    session: AutomationSession,
    job: AutomationJob,
    repository_connection_id: uuid.UUID,
    settings: Settings | None = None,
) -> PrWorkspaceReady:
    """
    Return repo root + current patch snapshot for ``run_session_pr_pipeline``.

    Reuses ``job.repo_path`` / ``session.repo_path`` when present and a valid git work tree.
    Otherwise rebuilds from ``repository_connection_id`` and the current ``AutomationPatchVersion``.
    """
    s = settings or get_settings()
    expire_due_workspace_cache_entries(db)

    patch_v, files = load_current_patch_files(db, session.id)

    candidate = (job.repo_path or session.repo_path or "").strip()
    if workspace_path_usable(candidate):
        mark_workspace_used(
            db,
            session_id=session.id,
            workspace_path=candidate,
            repository_connection_id=repository_connection_id,
            settings=s,
        )
        return PrWorkspaceReady(
            repo_path=str(Path(candidate).resolve()),
            patch_files=files,
            patch_version_id=patch_v.id,
            patch_version_number=int(patch_v.version_number),
        )

    job.repo_path = None
    session.repo_path = None
    db.flush()

    try:
        prep = prepare_automation_session_workspace(
            db,
            session=session,
            job=job,
            repository_connection_id=repository_connection_id,
            settings=s,
        )
    except RepoWorkspaceError as e:
        raise SourceControlRepoError(e.message, code=getattr(e, "code", "source_control_repo")) from e

    root_s = (job.repo_path or session.repo_path or prep.workspace_path or "").strip()
    if not root_s:
        raise SourceControlRepoError(
            "Workspace materialization did not set repo_path.",
            code="source_control_repo",
        )
    repo_root = Path(root_s).resolve()
    if not workspace_path_usable(repo_root):
        raise SourceControlRepoError(
            "Materialized workspace is not a usable git repository.",
            code="source_control_repo",
        )
    try:
        reapply_patch_for_pr_commit(repo_root, files)
    except WorkspaceApplyError as e:
        raise SourceControlRepoError(
            f"Failed to re-apply current patch after workspace rebuild: {e.message}",
            code="source_control_repo",
        ) from e

    _upsert_active_cache(
        db,
        session=session,
        workspace_path=str(repo_root),
        repository_connection_id=repository_connection_id,
        settings=s,
    )
    logger.info(
        "pr_workspace_rebuilt",
        extra={"session_id": str(session.id), "workspace_path": str(repo_root), "patch_version_id": str(patch_v.id)},
    )
    return PrWorkspaceReady(
        repo_path=str(repo_root),
        patch_files=files,
        patch_version_id=patch_v.id,
        patch_version_number=int(patch_v.version_number),
    )
