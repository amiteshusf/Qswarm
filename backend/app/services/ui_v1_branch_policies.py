"""
BFF normalization for ``/api/v1/branch-policies`` (Qswarm-UI ``branchPolicySchema``).

The UI client parses list responses as a **top-level JSON array** (see Qswarm-UI
``src/api/client.ts``). Each item matches ``branchPolicySchema`` in ``src/api/schemas.ts``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.schemas.repository_connection import BranchPolicyResponse


def _s(val: Any, *, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, str):
        return val
    return str(val)


def _policy_display_name(d: dict[str, Any]) -> str:
    pat = _s(d.get("branch_naming_pattern")).strip()
    if pat:
        return pat[:256]
    bid = _s(d.get("id"))
    return f"Policy {bid[:8]}" if bid else "Branch policy"


def format_branch_policy_json_for_ui(resp: BranchPolicyResponse) -> dict[str, Any]:
    d = resp.model_dump()
    out: dict[str, Any] = {
        "id": _s(d.get("id")),
        "name": _policy_display_name(d),
        "baseBranch": _s(d.get("base_branch_default")) or "main",
        "branchPattern": _s(d.get("branch_naming_pattern")) or "",
        "prTitleTemplate": _s(d.get("pr_title_template")) or "",
        "prBodyTemplate": _s(d.get("pr_body_template")) or "",
        "createdAt": d.get("created_at") or "",
        "updatedAt": d.get("updated_at") or "",
    }
    rc = d.get("repository_connection_id")
    if rc:
        out["repoConnectionId"] = _s(rc)
    return out


def branch_policy_id_for_connection(db: Session, *, repository_connection_id: Any) -> str | None:
    """Best-effort policy id for ``sessionDetailSchema.branchPolicyId`` (optional)."""
    if repository_connection_id is None:
        return None
    try:
        cid = uuid.UUID(str(repository_connection_id))
    except (ValueError, TypeError):
        return None
    pol = db.scalars(
        select(RepositoryBranchPolicy)
        .where(RepositoryBranchPolicy.repository_connection_id == cid)
        .order_by(RepositoryBranchPolicy.updated_at.desc())
        .limit(1)
    ).first()
    return str(pol.id) if pol else None
