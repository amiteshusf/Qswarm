"""Engine-agnostic workspace material-change detection for revision rounds."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.automation_engine.claude_workspace_patch import paths_for_revision_scope
from app.db.models.automation_job import AutomationJob
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path


class RevisionNoMaterialChangeError(Exception):
    """Revision engine run completed but scoped workspace files did not materially change."""

    code = "revision_no_material_change"

    def __init__(self, message: str, *, result: MaterialChangeResult):
        super().__init__(message)
        self.message = message
        self.result = result


@dataclass(frozen=True)
class WorkspaceFileEntry:
    path: str
    exists: bool
    byte_length: int
    sha256: str | None


@dataclass(frozen=True)
class MaterialChangeResult:
    scoped_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    before_hashes: dict[str, str | None]
    after_hashes: dict[str, str | None]
    has_material_change: bool
    failure_reason: str | None = None

    def to_audit_payload(self) -> dict[str, Any]:
        return {
            "scoped_paths": list(self.scoped_paths),
            "changed_paths": list(self.changed_paths),
            "unchanged_paths": list(self.unchanged_paths),
            "before_hashes": self.before_hashes,
            "after_hashes": self.after_hashes,
            "has_material_change": self.has_material_change,
        }


def resolve_revision_scoped_paths(job: AutomationJob, target_scope: str | None) -> list[str]:
    """Return normalized repo-relative paths in scope for a revision round."""
    return paths_for_revision_scope(job, target_scope)


def _norm_rel(p: str) -> str:
    return p.strip().replace("\\", "/")


def _hash_file_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def capture_workspace_snapshot(repo_root: Path, relative_paths: list[str]) -> dict[str, WorkspaceFileEntry]:
    """Snapshot existence, size, and sha256 for each scoped path under ``repo_root``."""
    root = repo_root.resolve()
    out: dict[str, WorkspaceFileEntry] = {}
    for raw in relative_paths:
        rel = _norm_rel(raw)
        if not rel or ".." in rel or rel.startswith("/"):
            continue
        dest = (root / rel).resolve()
        try:
            dest.relative_to(root)
        except ValueError:
            continue
        if not dest.is_file():
            out[rel] = WorkspaceFileEntry(path=rel, exists=False, byte_length=0, sha256=None)
            continue
        try:
            text = dest.read_text(encoding="utf-8")
        except OSError:
            out[rel] = WorkspaceFileEntry(path=rel, exists=True, byte_length=0, sha256=None)
            continue
        out[rel] = WorkspaceFileEntry(
            path=rel,
            exists=True,
            byte_length=len(text.encode("utf-8")),
            sha256=_hash_file_text(text),
        )
    return out


def compare_workspace_snapshots(
    before: dict[str, WorkspaceFileEntry],
    after: dict[str, WorkspaceFileEntry],
) -> MaterialChangeResult:
    """Compare scoped snapshots; material change when existence or content hash differs."""
    scoped = tuple(sorted(set(before.keys()) | set(after.keys())))
    changed: list[str] = []
    unchanged: list[str] = []
    before_hashes: dict[str, str | None] = {}
    after_hashes: dict[str, str | None] = {}

    for rel in scoped:
        b = before.get(rel)
        a = after.get(rel)
        before_hashes[rel] = b.sha256 if b else None
        after_hashes[rel] = a.sha256 if a else None
        b_exists = bool(b and b.exists)
        a_exists = bool(a and a.exists)
        b_hash = b.sha256 if b else None
        a_hash = a.sha256 if a else None
        if b_exists != a_exists or b_hash != a_hash:
            changed.append(rel)
        else:
            unchanged.append(rel)

    has_change = bool(changed)
    failure_reason = None
    if not has_change:
        failure_reason = (
            "Engine run completed but no scoped workspace file changed "
            f"({len(unchanged)} path(s) unchanged)."
        )
    return MaterialChangeResult(
        scoped_paths=scoped,
        changed_paths=tuple(changed),
        unchanged_paths=tuple(unchanged),
        before_hashes=before_hashes,
        after_hashes=after_hashes,
        has_material_change=has_change,
        failure_reason=failure_reason,
    )


def format_revision_no_material_change_message(result: MaterialChangeResult) -> str:
    paths = ", ".join(result.unchanged_paths[:12])
    extra = f" (+{len(result.unchanged_paths) - 12} more)" if len(result.unchanged_paths) > 12 else ""
    return (
        "Revision produced no material workspace change for scoped target file(s) "
        f"({paths}{extra}). The engine run may have exited successfully and tests may have passed, "
        "but no on-disk edits were detected. The instruction may already be satisfied, "
        "or the engine produced no code change."
    )


def require_material_workspace_change(
    repo_path: str | Path,
    *,
    before: dict[str, WorkspaceFileEntry],
    after: dict[str, WorkspaceFileEntry],
) -> MaterialChangeResult:
    """
    Raise :class:`RevisionNoMaterialChangeError` when scoped files are unchanged.

    Returns the comparison result when at least one scoped path materially changed.
    """
    _ = resolve_repo_path(str(repo_path))  # validate path shape
    result = compare_workspace_snapshots(before, after)
    if not result.has_material_change:
        raise RevisionNoMaterialChangeError(
            format_revision_no_material_change_message(result),
            result=result,
        )
    return result


def resolve_revision_workspace_root(job: AutomationJob, session_repo_path: str | None = None) -> Path:
    """Resolve the workspace root used for revision material-change snapshots."""
    raw = (job.repo_path or session_repo_path or "").strip()
    if not raw:
        raise FrameworkScanError("repo_path is required for revision material-change validation")
    return resolve_repo_path(raw)
