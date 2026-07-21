"""Claude Code CLI adapter — subprocess-based; workspace patch assembly after engine edits."""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path
from typing import Any

from app.automation_engine.base_adapter import CodingAgentAdapterBase
from app.automation_engine.cli_subprocess import run_subprocess_argv
from app.automation_engine.claude_workspace_patch import (
    build_full_generation_patch_from_workspace,
    build_repair_subset_patch_from_workspace,
    paths_for_revision_scope,
)
from app.automation_engine.coding_engine_names import CodingEngineName
from app.automation_engine.engine_errors import (
    EngineAdapterError,
    EngineConfigurationError,
    EngineMalformedOutputError,
    EngineRepoAccessError,
    EngineTimeoutError,
)
from app.automation_engine.engine_models import (
    EngineCapability,
    EngineRequest,
    EngineResult,
    EngineResultStatus,
    EngineTaskType,
)
from app.automation_engine.types import CodeSessionContext
from app.core.config import Settings, get_settings
from app.db.models.automation_job import AutomationJob
from app.services import automation_job_service
from app.services.automation_review_service import apply_review_revision_with_external_patch
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path


def _assert_request_matches_context(request: EngineRequest, ctx: CodeSessionContext) -> None:
    if request.session_id != str(ctx.session.id):
        raise ValueError("engine_request_session_mismatch")
    if request.job_id != str(ctx.job.id):
        raise ValueError("engine_request_job_mismatch")
    if request.round_id != str(ctx.revision_round.id):
        raise ValueError("engine_request_round_mismatch")


def _workspace_root(job: AutomationJob, request: EngineRequest) -> Path:
    rp = (job.repo_path or request.repo_path or "").strip()
    if not rp:
        raise EngineRepoAccessError(
            "repo_path is required for Claude Code (local workspace directory)",
            code="engine_repo_access",
        )
    try:
        return resolve_repo_path(rp)
    except FrameworkScanError as e:
        raise EngineRepoAccessError(e.message, code=getattr(e, "code", "engine_repo_access")) from e


def _resolve_cli_executable(settings: Settings) -> str:
    raw = (settings.qswarm_claude_code_command or "").strip()
    if not raw:
        raise EngineConfigurationError(
            "QSWARM_CLAUDE_CODE_COMMAND is empty",
            code="engine_configuration",
        )
    p = Path(raw)
    if p.is_file():
        return str(p.resolve())
    found = shutil.which(raw)
    if found:
        return found
    raise EngineConfigurationError(
        f"Claude CLI not found (not a file and not on PATH): {raw!r}",
        code="engine_configuration",
    )


def _build_argv(settings: Settings, prompt: str) -> list[str]:
    exe = _resolve_cli_executable(settings)
    extras = shlex.split((settings.qswarm_claude_code_extra_args or "").strip())
    if any(x in ("-p", "--print") for x in extras):
        return [exe, *extras, prompt]
    return [exe, *extras, "-p", prompt]


def _compose_prompt(request: EngineRequest, job: AutomationJob, *, mode: str) -> str:
    lines: list[str] = [
        "You are an expert test automation engineer working in the repository at cwd.",
        f"Mode: {mode}",
        "",
        "Implement the approved change plan by editing only the listed files on disk.",
        "Requirements:",
        "- Output valid source only in those files (no markdown fences in .ts files).",
        "- Do not add assistant-style prose inside source files.",
        "- Follow the existing Playwright patterns in this repo.",
        "",
    ]
    if request.source_reference:
        lines.append(f"Source reference: {request.source_reference}")
    if request.automation_goal:
        lines.append(f"Automation goal / case title: {request.automation_goal}")
    if request.plan_context:
        lines.append("Change plan (authoritative):")
        lines.append(_safe_json(request.plan_context))
    if request.existing_context:
        lines.append("Framework / repo context (hints):")
        lines.append(_safe_json(request.existing_context))
    if mode == "revision" and request.revision_instruction:
        lines.append("Revision instruction from reviewer:")
        lines.append(request.revision_instruction.strip())
    if request.target_scope:
        lines.append(f"Target scope hint: {request.target_scope.strip()}")
    if request.patch_context and mode == "revision":
        lines.append("Previous patch summary (reference):")
        lines.append(_safe_json(request.patch_context))
    return "\n".join(lines) + "\n"


def _safe_json(blob: dict[str, Any]) -> str:
    try:
        return json.dumps(blob, indent=2, default=str)[:120000]
    except Exception:
        return str(blob)[:120000]


def _run_cli(
    settings: Settings,
    *,
    argv: list[str],
    cwd: Path,
) -> dict[str, Any]:
    if not cwd.is_dir():
        raise EngineRepoAccessError(f"workspace is not a directory: {cwd}", code="engine_repo_access")
    timeout = float(settings.qswarm_claude_code_timeout_seconds)
    result = run_subprocess_argv(argv, cwd=cwd, timeout_seconds=timeout)
    if result.get("timed_out"):
        raise EngineTimeoutError(
            f"Claude CLI exceeded timeout ({settings.qswarm_claude_code_timeout_seconds}s)",
            code="engine_timeout",
        )
    code = result.get("exit_code")
    if code != 0:
        tail = (result.get("stderr") or result.get("stdout") or "")[-4000:]
        raise EngineAdapterError(
            f"Claude CLI exited with code {code}: {tail}",
            code="engine_cli_nonzero",
        )
    return result


def _plan_summary(plan: dict[str, Any] | None) -> str | None:
    if not isinstance(plan, dict) or not plan:
        return None
    tgt = plan.get("target_test_file")
    n_mod = len(plan.get("files_to_modify") or [])
    n_cre = len(plan.get("files_to_create") or [])
    return f"target={tgt!s}; modify={n_mod}; create={n_cre}"


def _patch_summary(patch: dict[str, Any] | None) -> str | None:
    if not isinstance(patch, dict):
        return None
    gf = patch.get("generated_files")
    if not isinstance(gf, list):
        return None
    paths = [str(x.get("path")) for x in gf if isinstance(x, dict)]
    return f"files={len(paths)}: {', '.join(paths[:12])}"


class ClaudeCodeAdapter(CodingAgentAdapterBase):
    """Runs the configured Claude Code CLI in ``repo_path``, then assembles patch content from disk."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings

    def _s(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    @property
    def engine_name(self) -> str:
        return CodingEngineName.CLAUDE_CODE.value

    def get_capabilities(self) -> EngineCapability:
        s = self._s()
        if not s.qswarm_claude_code_enabled:
            configured = False
        else:
            try:
                configured = self.validate_config()
            except EngineConfigurationError:
                configured = False
        allow_rev = bool(s.qswarm_claude_code_allow_revision)
        return EngineCapability(
            engine_name=self.engine_name,
            display_name="Claude Code (CLI)",
            configured=configured,
            supports_initial_request=True,
            supports_revision_request=allow_rev,
            supports_manual_rerun_request=True,
            supports_plan=True,
            supports_patch=True,
            supports_execution=True,
            supports_repo_context=True,
            supports_structured_output=True,
            supports_streaming=False,
            supports_pr_creation=False,
            notes=(
                "Subprocess CLI in repo cwd; patch bytes are read back from the workspace for "
                "QSwarm validation. Replace internals with an SDK later without changing this interface."
            ),
        )

    def validate_config(self) -> bool:
        s = self._s()
        if not s.qswarm_claude_code_enabled:
            return False
        if not (s.qswarm_claude_code_command or "").strip():
            raise EngineConfigurationError(
                "QSWARM_CLAUDE_CODE_COMMAND is empty while QSWARM_CLAUDE_CODE_ENABLED=true",
                code="engine_configuration",
            )
        _resolve_cli_executable(s)
        return True

    def _ensure_ready(self) -> None:
        if not self._s().qswarm_claude_code_enabled:
            raise EngineConfigurationError(
                "Claude Code is disabled (QSWARM_CLAUDE_CODE_ENABLED=false).",
                code="engine_configuration",
            )
        self.validate_config()

    def run_plan_only_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        self._ensure_ready()
        _assert_request_matches_context(request, context)
        db, job, aid = context.db, context.job, context.actor_id

        automation_job_service.start_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)
        automation_job_service.plan_automation_job_changes(db, job.id, actor_id=aid)
        db.refresh(job)

        plan = dict(job.change_plan_json or {})
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED,
            plan_summary=_plan_summary(plan),
            plan_payload=plan or None,
        )

    def run_execute_after_plan_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        self._ensure_ready()
        _assert_request_matches_context(request, context)
        db, job, aid = context.db, context.job, context.actor_id
        s = self._s()
        root = _workspace_root(job, request)
        plan = dict(job.change_plan_json or {})
        prompt = _compose_prompt(request, job, mode="initial_generation")
        argv = _build_argv(s, prompt)
        proc_meta = _run_cli(s, argv=argv, cwd=root)

        try:
            raw_patch = build_full_generation_patch_from_workspace(job, root, engine_run_label="claude_code")
        except FileNotFoundError as e:
            raise EngineMalformedOutputError(
                f"Expected plan file missing on disk after Claude run: {e}",
                code="engine_malformed_output",
            ) from e
        except ValueError as e:
            raise EngineMalformedOutputError(str(e), code="engine_malformed_output") from e

        automation_job_service.generate_code_from_external_patch(
            db, job.id, raw_patch, actor_id=aid, provider_label="claude_code"
        )
        db.refresh(job)
        automation_job_service.execute_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)

        ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
        cmd = ex.get("command")
        ok = bool(ex.get("success"))
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            plan_summary=_plan_summary(plan),
            patch_summary=_patch_summary(job.generated_patch_json if isinstance(job.generated_patch_json, dict) else None),
            plan_payload=plan or None,
            patch_payload=dict(job.generated_patch_json) if isinstance(job.generated_patch_json, dict) else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            raw_output=(proc_meta.get("stdout") or "")[-200000:] or None,
            error_message=None
            if ok
            else (job.blocked_reason or str((ex.get("notes") or [""])[0])),
            metadata={
                "argv": argv,
                "cwd": str(root),
                "exit_code": proc_meta.get("exit_code"),
                "duration_ms": proc_meta.get("duration_ms"),
                "stderr_tail": (proc_meta.get("stderr") or "")[-8000:],
                "working_mode": s.qswarm_claude_code_working_mode,
            },
        )

    def run_initial_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        self._ensure_ready()
        _assert_request_matches_context(request, context)
        db, job, aid = context.db, context.job, context.actor_id
        s = self._s()

        root = _workspace_root(job, request)

        automation_job_service.start_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)
        automation_job_service.plan_automation_job_changes(db, job.id, actor_id=aid)
        db.refresh(job)

        plan = dict(job.change_plan_json or {})
        prompt = _compose_prompt(request, job, mode="initial_generation")
        argv = _build_argv(s, prompt)
        proc_meta = _run_cli(s, argv=argv, cwd=root)

        try:
            raw_patch = build_full_generation_patch_from_workspace(job, root, engine_run_label="claude_code")
        except FileNotFoundError as e:
            raise EngineMalformedOutputError(
                f"Expected plan file missing on disk after Claude run: {e}",
                code="engine_malformed_output",
            ) from e
        except ValueError as e:
            raise EngineMalformedOutputError(str(e), code="engine_malformed_output") from e

        automation_job_service.generate_code_from_external_patch(
            db, job.id, raw_patch, actor_id=aid, provider_label="claude_code"
        )
        db.refresh(job)
        automation_job_service.execute_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)

        ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
        cmd = ex.get("command")
        ok = bool(ex.get("success"))
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            plan_summary=_plan_summary(plan),
            patch_summary=_patch_summary(job.generated_patch_json if isinstance(job.generated_patch_json, dict) else None),
            plan_payload=plan or None,
            patch_payload=dict(job.generated_patch_json) if isinstance(job.generated_patch_json, dict) else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            raw_output=(proc_meta.get("stdout") or "")[-200000:] or None,
            error_message=None
            if ok
            else (job.blocked_reason or str((ex.get("notes") or [""])[0])),
            metadata={
                "argv": argv,
                "cwd": str(root),
                "exit_code": proc_meta.get("exit_code"),
                "duration_ms": proc_meta.get("duration_ms"),
                "stderr_tail": (proc_meta.get("stderr") or "")[-8000:],
                "working_mode": s.qswarm_claude_code_working_mode,
            },
        )

    def run_revision_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        self._ensure_ready()
        _assert_request_matches_context(request, context)
        s = self._s()
        if not s.qswarm_claude_code_allow_revision:
            raise EngineConfigurationError(
                "Claude Code revisions are disabled (QSWARM_CLAUDE_CODE_ALLOW_REVISION=false).",
                code="engine_configuration",
            )
        inst = (request.revision_instruction or "").strip()
        if not inst:
            raise ValueError("revision_instruction_missing")

        db, job, aid = context.db, context.job, context.actor_id
        root = _workspace_root(job, request)

        automation_job_service.record_automation_job_revision_request(
            db, job.id, actor_id=aid, instruction_text=inst
        )
        db.refresh(job)

        prompt = _compose_prompt(request, job, mode="revision")
        argv = _build_argv(s, prompt)
        proc_meta = _run_cli(s, argv=argv, cwd=root)

        try:
            touched = paths_for_revision_scope(job, request.target_scope)
            raw_patch = build_repair_subset_patch_from_workspace(
                job, root, touched_paths=touched, engine_run_label="claude_code"
            )
        except FileNotFoundError as e:
            raise EngineMalformedOutputError(
                f"Expected file missing on disk after Claude revision: {e}",
                code="engine_malformed_output",
            ) from e
        except ValueError as e:
            raise EngineMalformedOutputError(str(e), code="engine_malformed_output") from e

        apply_review_revision_with_external_patch(
            db,
            job,
            instruction_text=inst,
            actor_id=aid,
            raw_patch=raw_patch,
            provider_label="claude_code",
        )
        db.refresh(job)

        ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
        patch = job.generated_patch_json if isinstance(job.generated_patch_json, dict) else {}
        cmd = ex.get("command")
        ok = bool(ex.get("success"))
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.REVISION,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            patch_summary=_patch_summary(patch),
            patch_payload=dict(patch) if patch else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            raw_output=(proc_meta.get("stdout") or "")[-200000:] or None,
            error_message=None if ok else (job.blocked_reason or str((ex.get("notes") or [""])[0])),
            metadata={
                "argv": argv,
                "cwd": str(root),
                "exit_code": proc_meta.get("exit_code"),
                "duration_ms": proc_meta.get("duration_ms"),
                "stderr_tail": (proc_meta.get("stderr") or "")[-8000:],
            },
        )

    def run_manual_rerun_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        """Manual re-exec uses the same job path as stub (no external CLI)."""
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
            metadata={"engine_note": "manual_rerun_no_cli"},
        )
