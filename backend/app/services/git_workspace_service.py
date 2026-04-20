"""Local git operations for automation PR flow (argv lists, no shell)."""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

DEFAULT_REMOTE = "origin"
GIT_TIMEOUT_S = 120


class GitWorkspaceError(Exception):
    """Raised when a git command fails or the repo is invalid."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _run_git(repo: Path, args: list[str], *, timeout: int = GIT_TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def ensure_git_repo(repo_path: str | Path) -> Path:
    """Verify ``repo_path`` is a directory and a git work tree."""
    p = Path(repo_path).resolve()
    if not p.is_dir():
        raise GitWorkspaceError("repo_path is not a directory")
    r = _run_git(p, ["rev-parse", "--is-inside-work-tree"])
    if r.returncode != 0:
        raise GitWorkspaceError((r.stderr or r.stdout or "not a git repository").strip())
    if r.stdout.strip().lower() != "true":
        raise GitWorkspaceError("not a git repository")
    return p


def has_git_remote(repo: Path, remote: str = DEFAULT_REMOTE) -> bool:
    r = _run_git(repo, ["remote", "get-url", remote])
    return r.returncode == 0


def job_branch_name(approved_case_id: str, job_id: uuid.UUID) -> str:
    """Deterministic local branch name: ``qswarm/<case-slug>-<job-prefix>``."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", approved_case_id.strip()).strip("-")[:48] or "case"
    return f"qswarm/{slug}-{str(job_id)[:8]}"


def _resolve_base_ref(repo: Path, base_branch: str, remote: str) -> str:
    """Return a ref that can be checked out or merged (local branch or remote tracking)."""
    a = _run_git(repo, ["rev-parse", "--verify", base_branch])
    if a.returncode == 0:
        return base_branch
    rem = f"{remote}/{base_branch}"
    b = _run_git(repo, ["rev-parse", "--verify", rem])
    if b.returncode == 0:
        return rem
    raise GitWorkspaceError(f"base branch not found locally or as {rem}")


def ensure_branch(repo: Path, branch_name: str, base_branch: str, remote: str = DEFAULT_REMOTE) -> None:
    """
    Ensure ``branch_name`` exists and is checked out, created from ``base_branch`` if needed.
    """
    ref_check = _run_git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"])
    if ref_check.returncode == 0:
        co = _run_git(repo, ["checkout", branch_name])
        if co.returncode != 0:
            raise GitWorkspaceError((co.stderr or co.stdout or "checkout failed").strip()[:2048])
        return

    base_ref = _resolve_base_ref(repo, base_branch, remote)
    co = _run_git(repo, ["checkout", "-b", branch_name, base_ref])
    if co.returncode != 0:
        raise GitWorkspaceError((co.stderr or co.stdout or "branch create failed").strip()[:2048])


def fetch_base_branch(repo: Path, base_branch: str, remote: str = DEFAULT_REMOTE) -> dict[str, Any]:
    """``git fetch`` for the base branch on ``remote`` when the remote exists."""
    if not has_git_remote(repo, remote):
        return {"fetched": False, "notes": [f"No git remote {remote!r}; skipped fetch"]}
    r = _run_git(repo, ["fetch", remote, base_branch])
    if r.returncode != 0:
        return {
            "fetched": False,
            "notes": [(r.stderr or r.stdout or "fetch failed").strip()[:500]],
        }
    return {"fetched": True, "notes": [f"Fetched {remote}/{base_branch}"]}


def list_unmerged_paths(repo: Path) -> list[str]:
    r = _run_git(repo, ["diff", "--name-only", "--diff-filter=U"])
    if r.returncode != 0:
        return []
    return [x.strip().replace("\\", "/") for x in r.stdout.splitlines() if x.strip()]


def refresh_branch_from_base(
    repo: Path,
    base_branch: str,
    remote: str = DEFAULT_REMOTE,
) -> dict[str, Any]:
    """
    Merge latest base into the current branch.

    Returns a structured result suitable for ``refresh_notes_json``.
    """
    merge_ref: str
    rem_ref = f"{remote}/{base_branch}"
    if _run_git(repo, ["rev-parse", "--verify", rem_ref]).returncode == 0:
        merge_ref = rem_ref
    elif _run_git(repo, ["rev-parse", "--verify", base_branch]).returncode == 0:
        merge_ref = base_branch
    else:
        return {
            "base_branch": base_branch,
            "updated": False,
            "conflicted": False,
            "conflict_files": [],
            "notes": [f"Neither {rem_ref} nor {base_branch} exists; cannot merge"],
        }

    before = _run_git(repo, ["rev-parse", "HEAD"])
    head_before = before.stdout.strip() if before.returncode == 0 else ""

    m = _run_git(repo, ["merge", merge_ref, "--no-edit"])
    out = (m.stdout or "") + (m.stderr or "")
    low = out.lower()

    if m.returncode != 0:
        conflicts = list_unmerged_paths(repo)
        return {
            "base_branch": base_branch,
            "updated": False,
            "conflicted": True,
            "conflict_files": conflicts[:50],
            "notes": [f"Merge conflict while merging {merge_ref}", out.strip()[:800]],
        }

    after = _run_git(repo, ["rev-parse", "HEAD"])
    head_after = after.stdout.strip() if after.returncode == 0 else ""
    already = "already up to date" in low
    updated = bool(head_before and head_after and head_before != head_after) and not already
    if already:
        updated = False
    notes = [out.strip()[:500] if out.strip() else f"Merged {merge_ref} into current branch"]
    return {
        "base_branch": base_branch,
        "updated": updated,
        "conflicted": False,
        "conflict_files": [],
        "notes": notes,
    }


def working_tree_has_changes(repo: Path) -> bool:
    r = _run_git(repo, ["status", "--porcelain"])
    if r.returncode != 0:
        raise GitWorkspaceError((r.stderr or "status failed").strip()[:500])
    return bool(r.stdout.strip())


def stage_all_changes(repo: Path) -> None:
    r = _run_git(repo, ["add", "-A"])
    if r.returncode != 0:
        raise GitWorkspaceError((r.stderr or "git add failed").strip()[:500])


def create_commit(repo: Path, message: str) -> None:
    msg = message.strip()[:5000] or "chore: qswarm automation"
    r = _run_git(repo, ["commit", "-m", msg])
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if "nothing to commit" in err.lower():
            raise GitWorkspaceError("nothing to commit")
        raise GitWorkspaceError(err[:2048])


def get_head_sha(repo: Path) -> str:
    r = _run_git(repo, ["rev-parse", "HEAD"])
    if r.returncode != 0:
        raise GitWorkspaceError((r.stderr or "rev-parse HEAD failed").strip()[:500])
    return r.stdout.strip()


def push_branch(repo: Path, branch_name: str, remote: str = DEFAULT_REMOTE) -> None:
    r = _run_git(repo, ["push", "-u", remote, branch_name])
    if r.returncode != 0:
        raise GitWorkspaceError((r.stderr or r.stdout or "git push failed").strip()[:2048])


def abort_merge_if_in_progress(repo: Path) -> None:
    """Best-effort ``git merge --abort`` when a merge is in progress."""
    merge_head = repo / ".git" / "MERGE_HEAD"
    if merge_head.is_file():
        _run_git(repo, ["merge", "--abort"])
