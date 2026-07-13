"""Apply validated generated files to a local repo workspace (no git worktrees)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any


class WorkspaceApplyError(Exception):
    """Raised when validated patch content cannot be written to disk."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _norm_rel(p: str) -> str:
    return p.strip().replace("\\", "/")


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    tmp = target.parent / f".qswarm_{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise WorkspaceApplyError(f"failed to write {target}: {e}") from e


def apply_generated_patch(repo_root: Path, generated_files: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Write ``generated_files`` under ``repo_root`` (paths must be repo-relative).

    ``modify`` requires an existing file; ``create`` requires the path not to exist.
    """
    root = repo_root.resolve()
    applied: list[dict[str, str]] = []

    for item in generated_files:
        rel = _norm_rel(str(item["path"]))
        if ".." in rel or rel.startswith("/"):
            raise WorkspaceApplyError(f"unsafe path: {rel}")
        dest = (root / rel).resolve()
        try:
            dest.relative_to(root)
        except ValueError as e:
            raise WorkspaceApplyError(f"path escapes workspace: {rel}") from e

        action = item["action"]
        content = item["content"]
        if not isinstance(content, str):
            raise WorkspaceApplyError(f"invalid content type for {rel}")

        if action == "modify":
            if not dest.is_file():
                raise WorkspaceApplyError(f"modify target missing: {rel}")
        elif action == "create":
            if dest.exists():
                raise WorkspaceApplyError(f"create target already exists: {rel}")
        else:
            raise WorkspaceApplyError(f"unknown action: {action}")

        try:
            _atomic_write_text(dest, content)
        except WorkspaceApplyError:
            raise
        except OSError as e:
            raise WorkspaceApplyError(f"failed to apply {rel}: {e}") from e

        applied.append({"path": rel, "action": action})

    return {
        "workspace_path": str(root),
        "applied_files": applied,
        "success": True,
    }


def reapply_patch_for_pr_commit(repo_root: Path, generated_files: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Idempotent PR handoff apply: write patch file bodies onto the current branch.

    Unlike :func:`apply_generated_patch`, overwrites/create-missing paths so a fresh
  branch checkout after ``ensure_branch`` can still receive the approved patch.
    """
    root = repo_root.resolve()
    applied: list[dict[str, str]] = []

    for item in generated_files:
        rel = _norm_rel(str(item["path"]))
        if ".." in rel or rel.startswith("/"):
            raise WorkspaceApplyError(f"unsafe path: {rel}")
        dest = (root / rel).resolve()
        try:
            dest.relative_to(root)
        except ValueError as e:
            raise WorkspaceApplyError(f"path escapes workspace: {rel}") from e

        content = item.get("content")
        if not isinstance(content, str):
            raise WorkspaceApplyError(f"invalid content type for {rel}")

        action = str(item.get("action") or "modify").strip() or "modify"
        try:
            _atomic_write_text(dest, content)
        except WorkspaceApplyError:
            raise
        except OSError as e:
            raise WorkspaceApplyError(f"failed to reapply {rel}: {e}") from e

        applied.append({"path": rel, "action": action})

    return {
        "workspace_path": str(root),
        "applied_files": applied,
        "success": True,
    }
