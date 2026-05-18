"""Repository connection + branch policy CRUD."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.constants import SourceControlProviderName
from app.db.models.repository_branch_policy import RepositoryBranchPolicy
from app.db.models.repository_connection import RepositoryConnection


def create_repository_connection(
    db: Session,
    *,
    provider: str,
    display_name: str,
    owner_or_org: str,
    repo_name: str,
    created_by: str,
    project_or_workspace: str | None = None,
    clone_url: str | None = None,
    default_branch: str = "main",
    auth_type: str = "github_pat_env",
    credential_reference: str | None = None,
    is_active: bool = True,
) -> RepositoryConnection:
    SourceControlProviderName.parse(provider)
    row = RepositoryConnection(
        provider=provider.strip().lower(),
        display_name=display_name.strip()[:256],
        owner_or_org=owner_or_org.strip()[:256],
        repo_name=repo_name.strip()[:256],
        project_or_workspace=(project_or_workspace.strip()[:256] if project_or_workspace else None),
        clone_url=(clone_url.strip()[:1024] if clone_url else None),
        default_branch=(default_branch.strip()[:256] or "main"),
        auth_type=(auth_type.strip()[:64] or "github_pat_env"),
        credential_reference=(credential_reference.strip()[:256] if credential_reference else None),
        is_active=is_active,
        created_by=created_by.strip()[:256],
    )
    db.add(row)
    db.flush()
    return row


def list_repository_connections(db: Session, *, active_only: bool = False) -> list[RepositoryConnection]:
    q = select(RepositoryConnection).order_by(RepositoryConnection.created_at.desc())
    if active_only:
        q = q.where(RepositoryConnection.is_active.is_(True))
    return list(db.scalars(q).all())


def get_repository_connection(db: Session, connection_id: uuid.UUID) -> RepositoryConnection | None:
    return db.get(RepositoryConnection, connection_id)


def update_repository_connection(
    db: Session,
    row: RepositoryConnection,
    *,
    patch: dict[str, Any],
) -> RepositoryConnection:
    allowed = {
        "display_name",
        "owner_or_org",
        "repo_name",
        "project_or_workspace",
        "clone_url",
        "default_branch",
        "auth_type",
        "credential_reference",
        "is_active",
    }
    for k, v in patch.items():
        if k not in allowed:
            continue
        if v is None and k in ("project_or_workspace", "clone_url", "credential_reference"):
            setattr(row, k, None)
        elif isinstance(v, str):
            setattr(row, k, v.strip()[:1024] if k == "clone_url" else v.strip()[:256])
        elif isinstance(v, bool) and k == "is_active":
            setattr(row, k, v)
    db.flush()
    return row


def upsert_branch_policy(
    db: Session,
    connection_id: uuid.UUID,
    *,
    base_branch_default: str = "main",
    branch_naming_pattern: str = "qswarm/{session_id}",
    allow_session_override: bool = True,
    commit_message_template: str | None = None,
    pr_title_template: str | None = None,
    pr_body_template: str | None = None,
    default_reviewers_json: dict[str, Any] | None = None,
    default_labels_json: list[Any] | None = None,
) -> RepositoryBranchPolicy:
    conn = db.get(RepositoryConnection, connection_id)
    if conn is None:
        raise ValueError("repository_connection_not_found")
    existing = db.scalar(
        select(RepositoryBranchPolicy).where(RepositoryBranchPolicy.repository_connection_id == connection_id)
    )
    if existing:
        existing.base_branch_default = base_branch_default.strip()[:256] or "main"
        existing.branch_naming_pattern = branch_naming_pattern.strip()[:512] or "qswarm/{session_id}"
        existing.allow_session_override = allow_session_override
        existing.commit_message_template = (
            commit_message_template.strip()[:512] if commit_message_template else None
        )
        existing.pr_title_template = pr_title_template.strip()[:512] if pr_title_template else None
        existing.pr_body_template = pr_body_template
        existing.default_reviewers_json = default_reviewers_json
        existing.default_labels_json = default_labels_json
        db.flush()
        return existing
    row = RepositoryBranchPolicy(
        repository_connection_id=connection_id,
        base_branch_default=base_branch_default.strip()[:256] or "main",
        branch_naming_pattern=branch_naming_pattern.strip()[:512] or "qswarm/{session_id}",
        allow_session_override=allow_session_override,
        commit_message_template=commit_message_template.strip()[:512] if commit_message_template else None,
        pr_title_template=pr_title_template.strip()[:512] if pr_title_template else None,
        pr_body_template=pr_body_template,
        default_reviewers_json=default_reviewers_json,
        default_labels_json=default_labels_json,
    )
    db.add(row)
    db.flush()
    return row


def get_branch_policy_for_connection(
    db: Session, connection_id: uuid.UUID
) -> RepositoryBranchPolicy | None:
    return db.scalar(
        select(RepositoryBranchPolicy).where(RepositoryBranchPolicy.repository_connection_id == connection_id)
    )


def connection_with_policy(db: Session, connection_id: uuid.UUID) -> RepositoryConnection | None:
    return db.scalar(
        select(RepositoryConnection)
        .where(RepositoryConnection.id == connection_id)
        .options(joinedload(RepositoryConnection.branch_policy))
    )
