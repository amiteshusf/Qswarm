"""Compare approved patch file bodies to a git base ref before PR commit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.git_workspace_service import (
    DEFAULT_REMOTE,
    GitWorkspaceError,
    ensure_git_repo,
    read_file_at_git_ref,
    resolve_base_branch_ref,
)
from app.services.workspace_service import _norm_rel


@dataclass(frozen=True)
class PatchBaseComparison:
    """Per-path comparison of patch content vs ``target_branch`` on disk."""

    target_branch: str
    base_ref: str
    identical_paths: tuple[str, ...]
    differing_paths: tuple[str, ...]
    new_paths: tuple[str, ...]
    missing_patch_content_paths: tuple[str, ...]

    @property
    def has_net_diff_against_base(self) -> bool:
        return bool(self.differing_paths or self.new_paths)

    @property
    def all_patch_files_match_base(self) -> bool:
        if self.missing_patch_content_paths:
            return False
        return not self.has_net_diff_against_base and bool(self.identical_paths)


def _git_show_text_at_ref(repo: Path, ref: str, rel_path: str) -> str | None:
    return read_file_at_git_ref(repo, ref, rel_path)


def compare_patch_files_to_base(
    repo_root: Path,
    target_branch: str,
    patch_files: list[dict[str, Any]],
    *,
    remote: str = DEFAULT_REMOTE,
) -> PatchBaseComparison:
    """
    Compare persisted patch bodies to the tree at ``target_branch`` without mutating the work tree.

    Raises:
        GitWorkspaceError: repo invalid or base ref cannot be resolved.
    """
    repo = ensure_git_repo(repo_root)
    base_ref = resolve_base_branch_ref(repo, target_branch.strip() or "main", remote)

    identical: list[str] = []
    differing: list[str] = []
    new_paths: list[str] = []
    missing_content: list[str] = []

    for item in patch_files:
        rel = _norm_rel(str(item.get("path") or ""))
        if not rel:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            missing_content.append(rel)
            continue
        base_text = _git_show_text_at_ref(repo, base_ref, rel)
        if base_text is None:
            new_paths.append(rel)
            continue
        if content == base_text:
            identical.append(rel)
        else:
            differing.append(rel)

    return PatchBaseComparison(
        target_branch=target_branch,
        base_ref=base_ref,
        identical_paths=tuple(identical),
        differing_paths=tuple(differing),
        new_paths=tuple(new_paths),
        missing_patch_content_paths=tuple(missing_content),
    )


def format_patch_identical_to_base_error(
    *,
    patch_version_number: int,
    patch_version_id: str,
    comparison: PatchBaseComparison,
) -> str:
    paths = ", ".join(comparison.identical_paths[:12])
    extra = f" (+{len(comparison.identical_paths) - 12} more)" if len(comparison.identical_paths) > 12 else ""
    return (
        f"Approved patch version {patch_version_number} (id={patch_version_id}) is identical to base branch "
        f"{comparison.target_branch!r} (ref {comparison.base_ref!r}) for all "
        f"{len(comparison.identical_paths)} file(s) ({paths}{extra}); no pull request is needed."
    )


def format_patch_no_git_diff_after_reapply_error(
    *,
    patch_version_number: int,
    patch_version_id: str,
    target_branch: str,
    comparison: PatchBaseComparison,
) -> str:
    diff_hint = ""
    if comparison.differing_paths or comparison.new_paths:
        parts: list[str] = []
        if comparison.differing_paths:
            parts.append(f"differing vs base: {', '.join(comparison.differing_paths[:6])}")
        if comparison.new_paths:
            parts.append(f"new vs base: {', '.join(comparison.new_paths[:6])}")
        diff_hint = f" Patch expected changes ({'; '.join(parts)}) but git working tree is clean after re-apply."
    paths = ", ".join(comparison.identical_paths[:8])
    extra = f" (+{len(comparison.identical_paths) - 8} more)" if len(comparison.identical_paths) > 8 else ""
    return (
        f"nothing to commit after re-applying current patch version {patch_version_number} "
        f"(id={patch_version_id}) onto branch refreshed from {target_branch!r}; "
        f"{len(comparison.identical_paths)} identical-to-base file(s) ({paths}{extra}).{diff_hint}"
    )
