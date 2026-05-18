"""Automation session API (Sprint 2 control plane)."""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.automation_engine.engine_errors import (
    EngineAdapterError,
    EngineConfigurationError,
    EngineMalformedOutputError,
    EngineRepoAccessError,
    EngineTimeoutError,
)
from app.automation_engine.registry import list_adapter_capabilities
from app.schemas.automation_session import (
    AutomationExecutionAttemptsListResponse,
    AutomationPatchVersionsListResponse,
    AutomationPlanVersionsListResponse,
    AutomationReviewRequestsListResponse,
    AutomationRevisionRoundsListResponse,
    AutomationSessionApproveBody,
    AutomationSessionCreateRequest,
    AutomationSessionManualAckBody,
    AutomationSessionRevisionBody,
    AutomationSessionSimpleResponse,
    AutomationSessionStartRequest,
    AutomationSessionStartResponse,
    AutomationSessionSummaryResponse,
    EngineCapabilitiesListResponse,
)
from app.schemas.repository_connection import (
    AutomationSessionCreatePrBody,
    AutomationSessionCreatePrResponse,
    CodeReviewRequestsListResponse,
)
from app.schemas.common import ErrorDetail, ErrorResponse
from app.services import automation_pr_service, automation_session_service
from app.services.automation_job_service import ChangePlanRejected, PatchRejected, WorkspaceApplyRejected
from app.services.framework_scan_service import FrameworkScanError
from app.source_control.errors import (
    SourceControlAuthError,
    SourceControlConfigurationError,
    SourceControlProviderError,
    SourceControlPushError,
    SourceControlRepoError,
    UnsupportedSourceControlProviderError,
)

router = APIRouter(prefix="/automation/sessions", tags=["automation"])


@router.get(
    "/engine-capabilities",
    response_model=EngineCapabilitiesListResponse,
)
def list_engine_capabilities():
    return EngineCapabilitiesListResponse(items=list_adapter_capabilities())


@router.post(
    "",
    response_model=AutomationSessionSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
)
def create_session(body: AutomationSessionCreateRequest, db: DbSession):
    try:
        sess = automation_session_service.create_automation_session(db, body)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("unsupported_coding_engine"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="unsupported_coding_engine",
                    message="Unknown coding_engine. Supported: stub, claude_code, copilot_agent.",
                ).model_dump(),
            ) from e
        if msg == "workflow_run_not_found":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="invalid_reference",
                    message="workflow_run_id does not exist",
                ).model_dump(),
            ) from e
        raise
    db.commit()
    db.refresh(sess)
    return AutomationSessionSummaryResponse.model_validate(
        automation_session_service.session_to_summary(db, sess)
    )


@router.get(
    "/{session_id}",
    response_model=AutomationSessionSummaryResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_session(session_id: uuid.UUID, db: DbSession):
    sess = automation_session_service.get_session(db, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    return AutomationSessionSummaryResponse.model_validate(
        automation_session_service.session_to_summary(db, sess)
    )


@router.get(
    "/{session_id}/rounds",
    response_model=AutomationRevisionRoundsListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_rounds(session_id: uuid.UUID, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    items = automation_session_service.list_rounds_for_api(db, session_id)
    return AutomationRevisionRoundsListResponse(items=items)


@router.get(
    "/{session_id}/plan-versions",
    response_model=AutomationPlanVersionsListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_plan_versions(session_id: uuid.UUID, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    return AutomationPlanVersionsListResponse(
        items=automation_session_service.list_plan_versions_for_api(db, session_id)
    )


@router.get(
    "/{session_id}/patch-versions",
    response_model=AutomationPatchVersionsListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_patch_versions(session_id: uuid.UUID, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    return AutomationPatchVersionsListResponse(
        items=automation_session_service.list_patch_versions_for_api(db, session_id)
    )


@router.get(
    "/{session_id}/execution-attempts",
    response_model=AutomationExecutionAttemptsListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_execution_attempts(session_id: uuid.UUID, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    return AutomationExecutionAttemptsListResponse(
        items=automation_session_service.list_execution_attempts_for_api(db, session_id)
    )


@router.get(
    "/{session_id}/review-requests",
    response_model=AutomationReviewRequestsListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_review_requests(session_id: uuid.UUID, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    return AutomationReviewRequestsListResponse(
        items=automation_session_service.list_review_requests_for_api(db, session_id)
    )


@router.post(
    "/{session_id}/start",
    response_model=AutomationSessionStartResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
def start_session(
    session_id: uuid.UUID,
    db: DbSession,
    body: AutomationSessionStartRequest | None = None,
):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    actor = (body.actor_id if body else None) or None
    try:
        sess = automation_session_service.start_automation_session(db, session_id, actor_id=actor)
    except EngineConfigurationError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(
                code="engine_configuration",
                message=e.message,
            ).model_dump(),
        ) from e
    except FrameworkScanError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except ChangePlanRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="invalid_change_plan", message=e.message).model_dump(),
        ) from e
    except PatchRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="invalid_generated_patch", message=e.message).model_dump(),
        ) from e
    except WorkspaceApplyRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="workspace_apply_failed", message=e.message).model_dump(),
        ) from e
    except EngineTimeoutError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except EngineRepoAccessError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except EngineMalformedOutputError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except EngineAdapterError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except ValueError as e:
        msg = str(e)
        if msg == "session_already_started":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="session_already_started",
                    message="This session has already been started",
                ).model_dump(),
            ) from e
        if msg == "job_not_pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Backing automation job is not in pending status",
                ).model_dump(),
            ) from e
        if msg == "actor_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(code="actor_missing", message="actor_id is required").model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(sess)
    summ = automation_session_service.session_to_summary(db, sess)
    job = summ.get("job_status")
    return AutomationSessionStartResponse(
        id=str(sess.id),
        status=summ["status"],
        job_status=job,
        message="Initial round completed (plan, patch, execute).",
    )


@router.post(
    "/{session_id}/request-revision",
    response_model=AutomationSessionSimpleResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
def request_revision(session_id: uuid.UUID, body: AutomationSessionRevisionBody, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    try:
        sess = automation_session_service.request_session_revision(
            db,
            session_id,
            actor_id=body.actor_id,
            instruction_text=body.instruction_text,
            target_scope=body.target_scope,
        )
    except EngineConfigurationError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code="engine_configuration", message=e.message).model_dump(),
        ) from e
    except EngineTimeoutError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except EngineRepoAccessError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except EngineMalformedOutputError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except EngineAdapterError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except PatchRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="invalid_generated_patch", message=e.message).model_dump(),
        ) from e
    except WorkspaceApplyRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="workspace_apply_failed", message=e.message).model_dump(),
        ) from e
    except ValueError as e:
        msg = str(e)
        if msg == "review_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Revision can only be requested when the job awaits automation review",
                ).model_dump(),
            ) from e
        if msg in ("revision_instruction_missing", "review_actor_missing"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(code=msg, message="actor_id and instruction_text are required").model_dump(),
            ) from e
        if msg == "review_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="review_prerequisites_missing",
                    message="Job prerequisites for review revision are not met",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(sess)
    summ = automation_session_service.session_to_summary(db, sess)
    return AutomationSessionSimpleResponse(
        id=str(sess.id),
        status=summ["status"],
        job_status=summ.get("job_status"),
        message="Revision round recorded.",
    )


@router.post(
    "/{session_id}/manual-edit-ack",
    response_model=AutomationSessionSimpleResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def manual_edit_ack(session_id: uuid.UUID, body: AutomationSessionManualAckBody, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    try:
        sess = automation_session_service.acknowledge_session_manual_edit(
            db, session_id, actor_id=body.actor_id, note=body.note
        )
    except EngineConfigurationError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code="engine_configuration", message=e.message).model_dump(),
        ) from e
    except ValueError as e:
        msg = str(e)
        if msg == "review_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job is not in a state that allows manual ack",
                ).model_dump(),
            ) from e
        if msg in ("manual_ack_note_missing", "review_actor_missing"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(code=msg, message="actor_id and note are required").model_dump(),
            ) from e
        if msg == "review_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(code=msg, message="Job prerequisites are not met").model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(sess)
    summ = automation_session_service.session_to_summary(db, sess)
    return AutomationSessionSimpleResponse(
        id=str(sess.id),
        status=summ["status"],
        job_status=summ.get("job_status"),
        message="Manual edit acknowledgement recorded.",
    )


@router.post(
    "/{session_id}/approve",
    response_model=AutomationSessionSimpleResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def approve_session(session_id: uuid.UUID, body: AutomationSessionApproveBody, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    try:
        sess = automation_session_service.approve_automation_session(
            db, session_id, actor_id=body.actor_id
        )
    except ValueError as e:
        msg = str(e)
        if msg == "review_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job can only be approved from awaiting_automation_review status",
                ).model_dump(),
            ) from e
        if msg == "review_actor_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(code=msg, message="actor_id is required").model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(sess)
    summ = automation_session_service.session_to_summary(db, sess)
    return AutomationSessionSimpleResponse(
        id=str(sess.id),
        status=summ["status"],
        job_status=summ.get("job_status"),
        message="Session approved for PR readiness.",
    )


@router.post(
    "/{session_id}/create-pr",
    response_model=AutomationSessionCreatePrResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
def create_pr_for_session(session_id: uuid.UUID, body: AutomationSessionCreatePrBody, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    try:
        row = automation_pr_service.create_pr_for_automation_session(
            db,
            session_id,
            actor_id=body.actor_id,
            repository_connection_id=body.repository_connection_id,
            target_branch=body.target_branch,
            source_branch=body.source_branch,
            title_override=body.title_override,
            body_override=body.body_override,
        )
    except ValueError as e:
        msg = str(e)
        if msg == "session_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
            ) from e
        if msg == "repository_connection_not_found":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="repository_connection_not_found",
                    message="Unknown or inactive repository_connection_id",
                ).model_dump(),
            ) from e
        if msg == "pr_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="PR creation requires approved_for_pr (or retry after pr_creation_failed)",
                ).model_dump(),
            ) from e
        if msg == "pr_already_created":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(code="pr_already_created", message="A PR was already created for this job").model_dump(),
            ) from e
        if msg == "branch_override_not_allowed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="branch_override_not_allowed",
                    message="Branch policy disallows target/source overrides for this connection",
                ).model_dump(),
            ) from e
        raise
    except UnsupportedSourceControlProviderError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except SourceControlConfigurationError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except SourceControlAuthError as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except (SourceControlRepoError, SourceControlPushError, SourceControlProviderError) as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ErrorDetail(code=getattr(e, "code", "source_control_provider_error"), message=e.message).model_dump(),
        ) from e

    db.commit()
    db.refresh(row)
    sess = automation_session_service.get_session(db, session_id)
    summ = automation_session_service.session_to_summary(db, sess) if sess else {}
    return AutomationSessionCreatePrResponse(
        id=str(session_id),
        status=summ.get("status", ""),
        job_status=summ.get("job_status"),
        code_review_request_id=str(row.id),
        external_url=row.external_url,
        external_id=row.external_id,
        message="Pull request created.",
    )


@router.get(
    "/{session_id}/code-review-requests",
    response_model=CodeReviewRequestsListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_code_review_requests(session_id: uuid.UUID, db: DbSession):
    if automation_session_service.get_session(db, session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation session not found").model_dump(),
        )
    items = automation_pr_service.list_code_review_requests_for_api(db, session_id)
    return CodeReviewRequestsListResponse(items=items)
