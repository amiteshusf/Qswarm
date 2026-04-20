"""Automation job API (Sprint 2)."""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.schemas.automation import (
    AutomationJobApproveRequest,
    AutomationJobApproveResponse,
    AutomationJobCreatePrRequest,
    AutomationJobCreatePrResponse,
    AutomationJobCreateRequest,
    AutomationJobExecuteResponse,
    AutomationJobGenerateResponse,
    AutomationJobListResponse,
    AutomationJobManualEditAckRequest,
    AutomationJobManualEditAckResponse,
    AutomationJobPlanResponse,
    AutomationJobRepairResponse,
    AutomationJobResponse,
    AutomationJobRevisionRequest,
    AutomationJobRevisionResponse,
    AutomationJobStartResponse,
)
from app.schemas.common import ErrorDetail, ErrorResponse
from app.services import automation_job_service
from app.services.framework_scan_service import FrameworkScanError

router = APIRouter(prefix="/automation", tags=["automation"])


@router.post(
    "/jobs",
    response_model=AutomationJobResponse,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}},
)
def create_job(body: AutomationJobCreateRequest, db: DbSession):
    try:
        job = automation_job_service.create_automation_job(db, body)
    except ValueError as e:
        if str(e) == "workflow_run_not_found":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="invalid_reference", message="workflow_run_id does not exist"
                ).model_dump(),
            ) from e
        raise
    db.commit()
    db.refresh(job)
    return AutomationJobResponse.model_validate(automation_job_service.job_to_response(job))


@router.get(
    "/jobs/{job_id}",
    response_model=AutomationJobResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_job(job_id: uuid.UUID, db: DbSession):
    job = automation_job_service.get_automation_job(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    return AutomationJobResponse.model_validate(automation_job_service.job_to_response(job))


@router.get("/jobs", response_model=AutomationJobListResponse)
def list_jobs(db: DbSession):
    rows = automation_job_service.list_automation_jobs(db)
    return AutomationJobListResponse(
        items=[AutomationJobResponse.model_validate(automation_job_service.job_to_response(j)) for j in rows]
    )


@router.post(
    "/jobs/{job_id}/start",
    response_model=AutomationJobStartResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def start_job(job_id: uuid.UUID, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.start_automation_job(
            db, job_id, actor_id=existing.requested_by
        )
    except FrameworkScanError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(code=e.code, message=e.message).model_dump(),
        ) from e
    except ValueError as e:
        msg = str(e)
        if msg == "job_not_startable":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job can only be started from pending status",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    msg = automation_job_service.describe_start_outcome(job)
    return AutomationJobStartResponse(id=job.id, status=job.status, message=msg)


@router.post(
    "/jobs/{job_id}/plan",
    response_model=AutomationJobPlanResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def plan_job(job_id: uuid.UUID, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.plan_automation_job_changes(
            db, job_id, actor_id=existing.requested_by
        )
    except automation_job_service.ChangePlanRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(
                code="invalid_change_plan",
                message=e.message,
            ).model_dump(),
        ) from e
    except ValueError as e:
        msg = str(e)
        if msg == "job_not_plan_ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job can only be planned from planning_changes status",
                ).model_dump(),
            ) from e
        if msg == "plan_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="plan_prerequisites_missing",
                    message="framework_summary_json, case_spec_json, and repo_context_json are required",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobPlanResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_plan_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/generate",
    response_model=AutomationJobGenerateResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def generate_job(job_id: uuid.UUID, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.generate_code_for_automation_job(
            db, job_id, actor_id=existing.requested_by
        )
    except automation_job_service.PatchRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="invalid_generated_patch", message=e.message).model_dump(),
        ) from e
    except automation_job_service.WorkspaceApplyRejected as e:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorDetail(code="workspace_apply_failed", message=e.message).model_dump(),
        ) from e
    except ValueError as e:
        msg = str(e)
        if msg == "job_not_generate_ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job can only be generated from generating_code status",
                ).model_dump(),
            ) from e
        if msg == "generation_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="generation_prerequisites_missing",
                    message="change_plan_json, framework/case/repo context, and repo_path are required",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobGenerateResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_generate_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/execute",
    response_model=AutomationJobExecuteResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def execute_job(job_id: uuid.UUID, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.execute_automation_job(
            db, job_id, actor_id=existing.requested_by
        )
    except ValueError as e:
        msg = str(e)
        if msg == "job_not_executable":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job can only be executed from executing status",
                ).model_dump(),
            ) from e
        if msg == "execution_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="execution_prerequisites_missing",
                    message="playwright framework summary, repo_path, change_plan or generated_patch "
                    "with target_test_file, and context JSON are required",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobExecuteResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_execute_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/repair",
    response_model=AutomationJobRepairResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def repair_job(job_id: uuid.UUID, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.repair_automation_job(
            db, job_id, actor_id=existing.requested_by
        )
    except ValueError as e:
        msg = str(e)
        if msg == "job_not_repairable_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Job can only be repaired from failed status",
                ).model_dump(),
            ) from e
        if msg == "repair_already_attempted":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="repair_already_attempted",
                    message="Repair flow was already run for this job",
                ).model_dump(),
            ) from e
        if msg == "repair_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="repair_prerequisites_missing",
                    message="Job must be failed with execution_result_json, Playwright framework, "
                    "change_plan_json, and repo_path to run repair",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobRepairResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_repair_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/approve",
    response_model=AutomationJobApproveResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def approve_job_for_pr(job_id: uuid.UUID, body: AutomationJobApproveRequest, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.approve_automation_job_for_pr(
            db, job_id, actor_id=body.actor_id
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
                detail=ErrorDetail(code="review_actor_missing", message="actor_id is required").model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobApproveResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_approve_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/request-revision",
    response_model=AutomationJobRevisionResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def request_revision(job_id: uuid.UUID, body: AutomationJobRevisionRequest, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.request_automation_job_revision(
            db,
            job_id,
            actor_id=body.actor_id,
            instruction_text=body.instruction_text,
        )
    except ValueError as e:
        msg = str(e)
        if msg == "review_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Revision can only be requested from awaiting_automation_review status",
                ).model_dump(),
            ) from e
        if msg in ("revision_instruction_missing", "review_actor_missing"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code=msg,
                    message="actor_id and non-empty instruction_text are required",
                ).model_dump(),
            ) from e
        if msg == "review_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="review_prerequisites_missing",
                    message="Playwright framework, repo_path, plan, and execution targets are required",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobRevisionResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_revision_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/manual-edit-ack",
    response_model=AutomationJobManualEditAckResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def manual_edit_ack(job_id: uuid.UUID, body: AutomationJobManualEditAckRequest, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    try:
        job = automation_job_service.acknowledge_manual_edit_and_rerun(
            db, job_id, actor_id=body.actor_id, note=body.note
        )
    except ValueError as e:
        msg = str(e)
        if msg == "review_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="Manual edit acknowledgement is only allowed from awaiting_automation_review "
                    "or failed status",
                ).model_dump(),
            ) from e
        if msg in ("manual_ack_note_missing", "review_actor_missing"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code=msg,
                    message="actor_id and non-empty note are required",
                ).model_dump(),
            ) from e
        if msg == "review_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="review_prerequisites_missing",
                    message="Playwright framework, repo_path, plan, and execution targets are required",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobManualEditAckResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_manual_ack_outcome(job),
    )


@router.post(
    "/jobs/{job_id}/create-pr",
    response_model=AutomationJobCreatePrResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
def create_pr(job_id: uuid.UUID, body: AutomationJobCreatePrRequest, db: DbSession):
    existing = automation_job_service.get_automation_job(db, job_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
        )
    actor = (body.actor_id or existing.requested_by or "").strip()
    try:
        job, pr_row = automation_job_service.create_pr_for_automation_job(
            db,
            job_id,
            actor_id=actor,
            repo_owner=body.repo_owner,
            repo_name=body.repo_name,
        )
    except ValueError as e:
        msg = str(e)
        if msg == "job_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorDetail(code="not_found", message="Automation job not found").model_dump(),
            ) from e
        if msg == "pr_wrong_state":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ErrorDetail(
                    code="invalid_state",
                    message="PR creation is only allowed from approved_for_pr status",
                ).model_dump(),
            ) from e
        if msg == "pr_prerequisites_missing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorDetail(
                    code="pr_prerequisites_missing",
                    message="GITHUB_TOKEN, GitHub repo owner/name (job fields or GITHUB_DEFAULT_*), "
                    "and a valid git repo_path are required",
                ).model_dump(),
            ) from e
        raise

    db.commit()
    db.refresh(job)
    return AutomationJobCreatePrResponse(
        id=job.id,
        status=job.status,
        message=automation_job_service.describe_create_pr_outcome(job, pr_row),
        pr_url=pr_row.pr_url,
        pr_number=pr_row.pr_number,
    )
