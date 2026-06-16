"""
UI BFF normalization for ``/api/v1/repo-connections``.

Aligned with **Qswarm-UI** ``src/api/schemas.ts`` / ``repoConnectionSchema`` and
``api.listRepoConnections()`` which parses the list response as ``z.array(repoConnectionSchema)``
(a **top-level JSON array**, not ``{ "repoConnections": [...] }``).

Each item uses ``owner``, ``repo``, ``authRef`` (not ``ownerOrOrg`` / ``repoName`` /
``authReference``). ``provider`` is restricted to ``github | gitlab | bitbucket | other``.
Optional UI fields ``displayName`` and ``cloneUrl`` are omitted when empty so JSON matches
optional Zod keys.

Legacy ``/repo-connections`` routes are unchanged.
"""

from __future__ import annotations

from typing import Any

from app.schemas.repository_connection import RepositoryConnectionResponse

_UI_REPO_PROVIDERS: frozenset[str] = frozenset({"github", "gitlab", "bitbucket", "other"})


def _ui_str(val: Any, *, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, str):
        return val
    return str(val)


def normalize_repo_provider_for_ui(provider: str) -> str:
    """Map stored provider slug into Qswarm-UI ``repoProviderSchema`` (e.g. ``azure_devops`` → ``other``)."""
    p = _ui_str(provider).strip().lower()
    if p in _UI_REPO_PROVIDERS:
        return p
    return "other"


def format_repo_connection_json_for_ui(resp: RepositoryConnectionResponse) -> dict[str, Any]:
    """Single object matching Qswarm-UI ``repoConnectionSchema``."""
    d = resp.model_dump()
    out: dict[str, Any] = {
        "id": _ui_str(d.get("id")),
        "provider": normalize_repo_provider_for_ui(_ui_str(d.get("provider"))),
        "owner": _ui_str(d.get("owner_or_org")),
        "repo": _ui_str(d.get("repo_name")),
        "defaultBranch": _ui_str(d.get("default_branch")) or "main",
        "authRef": _ui_str(d.get("credential_reference")),
        "createdAt": d.get("created_at") or "",
        "updatedAt": d.get("updated_at") or "",
    }
    dn = _ui_str(d.get("display_name"))
    if dn:
        out["displayName"] = dn
    cu = _ui_str(d.get("clone_url"))
    if cu:
        out["cloneUrl"] = cu
    return out
