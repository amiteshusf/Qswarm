"""Build normalized :class:`EngineRequest` from session/job/round state (no planning logic)."""

from __future__ import annotations

from typing import Any

from app.automation_engine.engine_models import EngineRequest, EngineTaskType
from app.core.config import Settings, get_settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession


class AutomationEnginePayloadBuilder:
    """Assembles engine-agnostic requests for adapters; does not run planning/generation."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _repo_url(self, session: AutomationSession) -> str | None:
        if session.repo_owner and session.repo_name:
            return f"https://github.com/{session.repo_owner.strip()}/{session.repo_name.strip()}"
        return None

    def _framework(self, job: AutomationJob) -> str | None:
        fw = job.framework_summary_json if isinstance(job.framework_summary_json, dict) else {}
        ft = fw.get("framework_type")
        return str(ft) if ft else None

    def _goal(self, job: AutomationJob) -> str | None:
        spec = job.case_spec_json if isinstance(job.case_spec_json, dict) else {}
        t = spec.get("title")
        return str(t) if t else None

    def _source_payload(self, job: AutomationJob) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if isinstance(job.case_spec_json, dict):
            out["case_spec"] = job.case_spec_json
        if isinstance(job.case_input_json, dict):
            out["case_input"] = job.case_input_json
        return out

    def build_initial_request(
        self,
        session: AutomationSession,
        job: AutomationJob,
        revision_round: AutomationRevisionRound,
        *,
        actor_id: str,
    ) -> EngineRequest:
        plan_ctx = job.change_plan_json if isinstance(job.change_plan_json, dict) else None
        patch_ctx = job.generated_patch_json if isinstance(job.generated_patch_json, dict) else None
        exec_ctx = job.execution_result_json if isinstance(job.execution_result_json, dict) else None
        return EngineRequest(
            session_id=str(session.id),
            job_id=str(job.id),
            round_id=str(revision_round.id),
            engine_name=session.coding_engine,
            task_type=EngineTaskType.INITIAL_GENERATION,
            source_type=session.source_system,
            source_reference=session.source_reference,
            source_payload=self._source_payload(job),
            repo_url=self._repo_url(session),
            repo_path=job.repo_path or session.repo_path,
            target_branch=job.base_branch or session.base_branch,
            workspace_path=job.repo_path or session.repo_path,
            automation_goal=self._goal(job),
            test_framework=self._framework(job),
            language=None,
            existing_context={
                "framework_summary": job.framework_summary_json,
                "repo_context": job.repo_context_json,
            },
            plan_context=plan_ctx,
            patch_context=patch_ctx,
            execution_context=exec_ctx,
            revision_instruction=None,
            target_scope=None,
            requested_by=actor_id,
            metadata={
                "playwright_timeout_seconds": self._settings.playwright_execution_timeout_seconds,
            },
        )

    def build_revision_request(
        self,
        session: AutomationSession,
        job: AutomationJob,
        revision_round: AutomationRevisionRound,
        *,
        actor_id: str,
        instruction_text: str,
        target_scope: str | None,
    ) -> EngineRequest:
        plan_ctx = job.change_plan_json if isinstance(job.change_plan_json, dict) else None
        patch_ctx = job.generated_patch_json if isinstance(job.generated_patch_json, dict) else None
        exec_ctx = job.execution_result_json if isinstance(job.execution_result_json, dict) else None
        return EngineRequest(
            session_id=str(session.id),
            job_id=str(job.id),
            round_id=str(revision_round.id),
            engine_name=session.coding_engine,
            task_type=EngineTaskType.REVISION,
            source_type=session.source_system,
            source_reference=session.source_reference,
            source_payload=self._source_payload(job),
            repo_url=self._repo_url(session),
            repo_path=job.repo_path or session.repo_path,
            target_branch=job.base_branch or session.base_branch,
            workspace_path=job.repo_path or session.repo_path,
            automation_goal=self._goal(job),
            test_framework=self._framework(job),
            existing_context={
                "framework_summary": job.framework_summary_json,
                "repo_context": job.repo_context_json,
            },
            plan_context=plan_ctx,
            patch_context=patch_ctx,
            execution_context=exec_ctx,
            revision_instruction=instruction_text.strip(),
            target_scope=target_scope.strip() if target_scope else None,
            requested_by=actor_id,
            metadata={},
        )

    def build_manual_rerun_request(
        self,
        session: AutomationSession,
        job: AutomationJob,
        revision_round: AutomationRevisionRound,
        *,
        actor_id: str,
        note: str,
    ) -> EngineRequest:
        plan_ctx = job.change_plan_json if isinstance(job.change_plan_json, dict) else None
        patch_ctx = job.generated_patch_json if isinstance(job.generated_patch_json, dict) else None
        exec_ctx = job.execution_result_json if isinstance(job.execution_result_json, dict) else None
        return EngineRequest(
            session_id=str(session.id),
            job_id=str(job.id),
            round_id=str(revision_round.id),
            engine_name=session.coding_engine,
            task_type=EngineTaskType.MANUAL_RERUN,
            source_type=session.source_system,
            source_reference=session.source_reference,
            source_payload=self._source_payload(job),
            repo_url=self._repo_url(session),
            repo_path=job.repo_path or session.repo_path,
            target_branch=job.base_branch or session.base_branch,
            workspace_path=job.repo_path or session.repo_path,
            automation_goal=self._goal(job),
            test_framework=self._framework(job),
            existing_context={
                "framework_summary": job.framework_summary_json,
                "repo_context": job.repo_context_json,
            },
            plan_context=plan_ctx,
            patch_context=patch_ctx,
            execution_context=exec_ctx,
            revision_instruction=note.strip(),
            target_scope=None,
            requested_by=actor_id,
            metadata={},
        )


def build_initial_engine_request(
    session: AutomationSession,
    job: AutomationJob,
    revision_round: AutomationRevisionRound,
    *,
    actor_id: str,
    settings: Settings | None = None,
) -> EngineRequest:
    return AutomationEnginePayloadBuilder(settings).build_initial_request(
        session, job, revision_round, actor_id=actor_id
    )


def build_revision_engine_request(
    session: AutomationSession,
    job: AutomationJob,
    revision_round: AutomationRevisionRound,
    *,
    actor_id: str,
    instruction_text: str,
    target_scope: str | None,
    settings: Settings | None = None,
) -> EngineRequest:
    return AutomationEnginePayloadBuilder(settings).build_revision_request(
        session,
        job,
        revision_round,
        actor_id=actor_id,
        instruction_text=instruction_text,
        target_scope=target_scope,
    )


def build_manual_rerun_engine_request(
    session: AutomationSession,
    job: AutomationJob,
    revision_round: AutomationRevisionRound,
    *,
    actor_id: str,
    note: str,
    settings: Settings | None = None,
) -> EngineRequest:
    return AutomationEnginePayloadBuilder(settings).build_manual_rerun_request(
        session, job, revision_round, actor_id=actor_id, note=note
    )
