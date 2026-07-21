"""Stub coding engine: delegates to existing Sprint 2 job services (CODING_PROVIDER=stub)."""

from __future__ import annotations

from app.automation_engine.base_adapter import CodingAgentAdapterBase
from app.automation_engine.coding_engine_names import CodingEngineName
from app.automation_engine.engine_models import (
    EngineCapability,
    EngineRequest,
    EngineResult,
    EngineResultStatus,
    EngineTaskType,
)
from app.automation_engine.types import CodeSessionContext, PatchResult, PlanResult
from app.services import automation_job_service


def _assert_request_matches_context(request: EngineRequest, ctx: CodeSessionContext) -> None:
    if request.session_id != str(ctx.session.id):
        raise ValueError("engine_request_session_mismatch")
    if request.job_id != str(ctx.job.id):
        raise ValueError("engine_request_job_mismatch")
    if request.round_id != str(ctx.revision_round.id):
        raise ValueError("engine_request_round_mismatch")


class StubCodingAgentAdapter(CodingAgentAdapterBase):
    """Uses current ``CODING_PROVIDER=stub`` path via ``automation_job_service``."""

    @property
    def engine_name(self) -> str:
        return CodingEngineName.STUB.value

    def get_capabilities(self) -> EngineCapability:
        return EngineCapability(
            engine_name=self.engine_name,
            display_name="QSwarm stub coding engine",
            configured=True,
            supports_initial_request=True,
            supports_revision_request=True,
            supports_manual_rerun_request=True,
            supports_plan=True,
            supports_patch=True,
            supports_execution=True,
            supports_repo_context=True,
            supports_structured_output=True,
            supports_streaming=False,
            supports_pr_creation=False,
            notes="Deterministic local provider; no external API.",
        )

    def validate_config(self) -> bool:
        return True

    def build_plan(self, ctx: CodeSessionContext) -> PlanResult:
        automation_job_service.plan_automation_job_changes(
            ctx.db, ctx.job.id, actor_id=ctx.actor_id
        )
        ctx.db.refresh(ctx.job)
        return PlanResult(plan_json=dict(ctx.job.change_plan_json or {}))

    def generate_patch(self, ctx: CodeSessionContext) -> PatchResult:
        automation_job_service.generate_code_for_automation_job(
            ctx.db, ctx.job.id, actor_id=ctx.actor_id
        )
        ctx.db.refresh(ctx.job)
        return PatchResult(patch_json=dict(ctx.job.generated_patch_json or {}))

    def run_plan_only_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        _assert_request_matches_context(request, context)
        db, job, aid = context.db, context.job, context.actor_id

        automation_job_service.start_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)
        plan = self.build_plan(context)
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED,
            plan_payload=plan.plan_json,
        )

    def run_execute_after_plan_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        _assert_request_matches_context(request, context)
        db, job, aid = context.db, context.job, context.actor_id

        patch = self.generate_patch(context)
        automation_job_service.execute_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)

        ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
        cmd = ex.get("command")
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED if job.execution_result_json and ex.get("success") else EngineResultStatus.FAILED,
            patch_payload=patch.patch_json,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            error_message=None if ex.get("success") else (str((ex.get("notes") or [""])[0]) if ex.get("notes") else job.blocked_reason),
        )

    def run_initial_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        _assert_request_matches_context(request, context)
        db, job, aid = context.db, context.job, context.actor_id

        automation_job_service.start_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)

        plan = self.build_plan(context)
        patch = self.generate_patch(context)

        automation_job_service.execute_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)

        ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
        cmd = ex.get("command")
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED if job.execution_result_json and ex.get("success") else EngineResultStatus.FAILED,
            plan_payload=plan.plan_json,
            patch_payload=patch.patch_json,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            error_message=None if ex.get("success") else (str((ex.get("notes") or [""])[0]) if ex.get("notes") else job.blocked_reason),
        )

    def run_revision_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        _assert_request_matches_context(request, context)
        inst = (request.revision_instruction or "").strip()
        if not inst:
            raise ValueError("revision_instruction_missing")
        automation_job_service.request_automation_job_revision(
            context.db,
            context.job.id,
            actor_id=context.actor_id.strip(),
            instruction_text=inst,
        )
        context.db.refresh(context.job)
        ex = context.job.execution_result_json if isinstance(context.job.execution_result_json, dict) else {}
        patch = context.job.generated_patch_json if isinstance(context.job.generated_patch_json, dict) else {}
        cmd = ex.get("command")
        ok = bool(ex.get("success"))
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.REVISION,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            patch_payload=dict(patch) if patch else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            error_message=None if ok else (context.job.blocked_reason or str((ex.get("notes") or [""])[0])),
        )

    def run_manual_rerun_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        _assert_request_matches_context(request, context)
        note = (request.revision_instruction or "").strip()
        if not note:
            raise ValueError("manual_rerun_note_missing")
        automation_job_service.acknowledge_manual_edit_and_rerun(
            context.db,
            context.job.id,
            actor_id=context.actor_id.strip(),
            note=note,
        )
        context.db.refresh(context.job)
        ex = context.job.execution_result_json if isinstance(context.job.execution_result_json, dict) else {}
        cmd = ex.get("command")
        ok = bool(ex.get("success"))
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.MANUAL_RERUN,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            error_message=None if ok else (context.job.blocked_reason or str((ex.get("notes") or [""])[0])),
        )


# Backward-compatible alias
StubCodeAgentAdapter = StubCodingAgentAdapter
