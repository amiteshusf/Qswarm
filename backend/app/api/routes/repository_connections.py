"""Repository connection and branch policy APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.schemas.common import ErrorDetail, ErrorResponse
from app.schemas.repository_connection import (
    BranchPolicyCreateRequest,
    BranchPolicyPatchRequest,
    BranchPolicyResponse,
    RepositoryConnectionCreateRequest,
    RepositoryConnectionPatchRequest,
    RepositoryConnectionResponse,
    RepositoryConnectionsListResponse,
)
from app.services import repository_connection_service

router = APIRouter(prefix="/repo-connections", tags=["repository-connections"])


def repository_connection_to_response(row) -> RepositoryConnectionResponse:
    return RepositoryConnectionResponse(
        id=str(row.id),
        provider=row.provider,
        display_name=row.display_name,
        owner_or_org=row.owner_or_org,
        repo_name=row.repo_name,
        project_or_workspace=row.project_or_workspace,
        clone_url=row.clone_url,
        default_branch=row.default_branch,
        auth_type=row.auth_type,
        credential_reference=row.credential_reference,
        is_active=row.is_active,
        created_by=row.created_by,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def branch_policy_to_response(p) -> BranchPolicyResponse:
    return BranchPolicyResponse(
        id=str(p.id),
        repository_connection_id=str(p.repository_connection_id),
        base_branch_default=p.base_branch_default,
        branch_naming_pattern=p.branch_naming_pattern,
        allow_session_override=p.allow_session_override,
        commit_message_template=p.commit_message_template,
        pr_title_template=p.pr_title_template,
        pr_body_template=p.pr_body_template,
        default_reviewers_json=p.default_reviewers_json,
        default_labels_json=p.default_labels_json,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


@router.post("", response_model=RepositoryConnectionResponse, status_code=status.HTTP_201_CREATED)
def create_repo_connection(body: RepositoryConnectionCreateRequest, db: DbSession):
    try:
        row = repository_connection_service.create_repository_connection(
            db,
            provider=body.provider,
            display_name=body.display_name,
            owner_or_org=body.owner_or_org,
            repo_name=body.repo_name,
            created_by=body.created_by,
            project_or_workspace=body.project_or_workspace,
            clone_url=body.clone_url,
            default_branch=body.default_branch,
            auth_type=body.auth_type,
            credential_reference=body.credential_reference,
            is_active=body.is_active,
        )
    except ValueError as e:
        if str(e).startswith("unsupported_source_control_provider"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(code="unsupported_provider", message=str(e)).model_dump(),
            ) from e
        raise
    db.commit()
    db.refresh(row)
    return repository_connection_to_response(row)


@router.get("", response_model=RepositoryConnectionsListResponse)
def list_repo_connections(db: DbSession):
    rows = repository_connection_service.list_repository_connections(db)
    return RepositoryConnectionsListResponse(items=[repository_connection_to_response(r) for r in rows])


@router.get("/{connection_id}", response_model=RepositoryConnectionResponse, responses={404: {"model": ErrorResponse}})
def get_repo_connection(connection_id: uuid.UUID, db: DbSession):
    row = repository_connection_service.get_repository_connection(db, connection_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Repository connection not found").model_dump(),
        )
    return repository_connection_to_response(row)


@router.patch("/{connection_id}", response_model=RepositoryConnectionResponse, responses={404: {"model": ErrorResponse}})
def patch_repo_connection(connection_id: uuid.UUID, body: RepositoryConnectionPatchRequest, db: DbSession):
    row = repository_connection_service.get_repository_connection(db, connection_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Repository connection not found").model_dump(),
        )
    patch = body.model_dump(exclude_unset=True)
    repository_connection_service.update_repository_connection(db, row, patch=patch)
    db.commit()
    db.refresh(row)
    return repository_connection_to_response(row)


@router.post(
    "/{connection_id}/branch-policy",
    response_model=BranchPolicyResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
def create_branch_policy(connection_id: uuid.UUID, body: BranchPolicyCreateRequest, db: DbSession):
    try:
        p = repository_connection_service.upsert_branch_policy(
            db,
            connection_id,
            base_branch_default=body.base_branch_default,
            branch_naming_pattern=body.branch_naming_pattern,
            allow_session_override=body.allow_session_override,
            commit_message_template=body.commit_message_template,
            pr_title_template=body.pr_title_template,
            pr_body_template=body.pr_body_template,
            default_reviewers_json=body.default_reviewers_json,
            default_labels_json=body.default_labels_json,
        )
    except ValueError as e:
        if str(e) == "repository_connection_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Repository connection not found").model_dump(),
            ) from e
        raise
    db.commit()
    db.refresh(p)
    return branch_policy_to_response(p)


@router.get(
    "/{connection_id}/branch-policy",
    response_model=BranchPolicyResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_branch_policy(connection_id: uuid.UUID, db: DbSession):
    p = repository_connection_service.get_branch_policy_for_connection(db, connection_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Branch policy not found").model_dump(),
        )
    return branch_policy_to_response(p)


@router.patch(
    "/{connection_id}/branch-policy",
    response_model=BranchPolicyResponse,
    responses={404: {"model": ErrorResponse}},
)
def patch_branch_policy(connection_id: uuid.UUID, body: BranchPolicyPatchRequest, db: DbSession):
    p = repository_connection_service.get_branch_policy_for_connection(db, connection_id)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Branch policy not found").model_dump(),
        )
    data = body.model_dump(exclude_unset=True)
    if "base_branch_default" in data and data["base_branch_default"]:
        p.base_branch_default = data["base_branch_default"].strip()[:256]
    if "branch_naming_pattern" in data and data["branch_naming_pattern"]:
        p.branch_naming_pattern = data["branch_naming_pattern"].strip()[:512]
    if "allow_session_override" in data and data["allow_session_override"] is not None:
        p.allow_session_override = bool(data["allow_session_override"])
    if "commit_message_template" in data:
        p.commit_message_template = (
            data["commit_message_template"].strip()[:512] if data["commit_message_template"] else None
        )
    if "pr_title_template" in data:
        p.pr_title_template = data["pr_title_template"].strip()[:512] if data["pr_title_template"] else None
    if "pr_body_template" in data:
        p.pr_body_template = data["pr_body_template"]
    if "default_reviewers_json" in data:
        p.default_reviewers_json = data["default_reviewers_json"]
    if "default_labels_json" in data:
        p.default_labels_json = data["default_labels_json"]
    db.commit()
    db.refresh(p)
    return branch_policy_to_response(p)
