"""GitHub Copilot CLI adapter — subprocess-based; workspace patch from disk after engine edits.

Manual rerun: no Copilot CLI invocation; QSwarm re-executes Playwright against the current workspace
via ``acknowledge_manual_edit_and_rerun`` (same contract as :class:`ClaudeCodeAdapter`).
TODO: swap CLI subprocess internals for Copilot SDK or cloud-agent without changing adapter surface.
"""

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
from app.core.constants import ActorType, AuditEventType
from app.db.models.automation_job import AutomationJob
from app.services import audit_service, automation_job_service
from app.services.automation_review_service import apply_review_revision_with_external_patch
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path

_COPILOT_PROMPT_FLAGS = frozenset({"-p", "--print", "--prompt"})
_STDOUT_TAIL_MAX = 8000
_STDERR_TAIL_MAX = 8000


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
            "repo_path is required for Copilot CLI (local workspace directory)",
            code="engine_repo_access",
        )
    try:
        return resolve_repo_path(rp)
    except FrameworkScanError as e:
        raise EngineRepoAccessError(e.message, code=getattr(e, "code", "engine_repo_access")) from e


def _resolve_cli_executable(settings: Settings) -> str:
    raw = (settings.qswarm_copilot_agent_command or "").strip()
    if not raw:
        raise EngineConfigurationError(
            "QSWARM_COPILOT_AGENT_COMMAND is empty",
            code="engine_configuration",
        )
    p = Path(raw)
    if p.is_file():
        return str(p.resolve())
    found = shutil.which(raw)
    if found:
        return found
    raise EngineConfigurationError(
        f"Copilot CLI not found (not a file and not on PATH): {raw!r}",
        code="engine_configuration",
    )


def parse_copilot_extra_args(extra_args: str | None) -> list[str]:
    """POSIX ``shlex.split`` of ``QSWARM_COPILOT_AGENT_EXTRA_ARGS`` (empty → no tokens)."""
    raw = (extra_args or "").strip()
    if not raw:
        return []
    return shlex.split(raw, posix=True)


def build_copilot_cli_argv(settings: Settings, prompt: str) -> tuple[list[str], dict[str, Any]]:
    """
    Build Copilot subprocess argv: ``COMMAND + EXTRA_ARGS + (-p|PROMPT_FLAG) + prompt``.

    When EXTRA_ARGS already includes ``-p``, ``--print``, or ``--prompt``, QSwarm appends the
    task prompt as the final argv element and does not inject another prompt flag.
    """
    exe = _resolve_cli_executable(settings)
    extras = parse_copilot_extra_args(settings.qswarm_copilot_agent_extra_args)
    prompt_flag = next((x for x in extras if x in _COPILOT_PROMPT_FLAGS), None)
    if prompt_flag is None:
        prompt_flag = "-p"
        argv = [exe, *extras, prompt_flag, prompt]
    else:
        argv = [exe, *extras, prompt]
    invocation = {
        "executable": exe,
        "extra_args": list(extras),
        "extra_args_raw": (settings.qswarm_copilot_agent_extra_args or "").strip(),
        "prompt_flag": prompt_flag,
        "argv_prefix": [exe, *extras, prompt_flag],
        "prompt_char_count": len(prompt),
        "timeout_seconds": int(settings.qswarm_copilot_agent_timeout_seconds),
    }
    return argv, invocation


def summarize_copilot_argv(argv: list[str]) -> dict[str, Any]:
    """Safe argv summary for logs/audit — omits the final prompt text when present."""
    if not argv:
        return {"argv_prefix": [], "prompt_char_count": 0}
    last = argv[-1]
    has_trailing_prompt = (
        len(argv) >= 2
        and argv[-2] in _COPILOT_PROMPT_FLAGS
        and isinstance(last, str)
        and len(last) > 200
    )
    if has_trailing_prompt:
        return {
            "argv_prefix": argv[:-1],
            "prompt_char_count": len(last),
        }
    return {"argv_prefix": list(argv), "prompt_char_count": 0}


def _build_cli_run_metadata(
    invocation: dict[str, Any],
    proc_meta: dict[str, Any],
    *,
    cwd: Path,
) -> dict[str, Any]:
    stdout = proc_meta.get("stdout") or ""
    stderr = proc_meta.get("stderr") or ""
    return {
        **invocation,
        "argv_summary": summarize_copilot_argv(
            [*invocation.get("argv_prefix", []), "<prompt>"]
            if invocation.get("prompt_char_count")
            else invocation.get("argv_prefix", [])
        ),
        "cwd": str(cwd),
        "exit_code": proc_meta.get("exit_code"),
        "duration_ms": proc_meta.get("duration_ms"),
        "stdout_tail": stdout[-_STDOUT_TAIL_MAX:] if stdout else "",
        "stderr_tail": stderr[-_STDERR_TAIL_MAX:] if stderr else "",
        "cli_kind": "copilot_cli",
    }


def _audit_copilot_cli(
    db: Any,
    job: AutomationJob,
    actor_id: str,
    *,
    phase: str,
    task_type: EngineTaskType,
    payload: dict[str, Any],
) -> None:
    if phase == "started":
        event_type = AuditEventType.AUTOMATION_CODE_GENERATION_STARTED.value
    elif phase == "completed":
        event_type = AuditEventType.AUTOMATION_CODE_GENERATED.value
    else:
        event_type = AuditEventType.AUTOMATION_PATCH_VALIDATION_FAILED.value
    audit_service.write_audit(
        db,
        event_type=event_type,
        actor_type=ActorType.SYSTEM.value,
        actor_id=(actor_id or job.requested_by or "system")[:256],
        workflow_run_id=job.workflow_run_id,
        step_name="copilot_cli",
        entity_type="automation_job",
        entity_id=str(job.id),
        payload={"engine": "copilot_agent", "task_type": task_type.value, "phase": phase, **payload},
    )


def _invoke_copilot_cli(
    db: Any,
    job: AutomationJob,
    actor_id: str,
    settings: Settings,
    *,
    root: Path,
    prompt: str,
    task_type: EngineTaskType,
) -> tuple[dict[str, Any], dict[str, Any]]:
    argv, invocation = build_copilot_cli_argv(settings, prompt)
    start_payload = {
        **invocation,
        "argv_summary": summarize_copilot_argv(argv),
    }
    _audit_copilot_cli(db, job, actor_id, phase="started", task_type=task_type, payload=start_payload)
    db.flush()
    try:
        proc_meta = _run_cli(settings, argv=argv, cwd=root)
    except (EngineTimeoutError, EngineAdapterError) as exc:
        fail_payload = {
            **start_payload,
            "success": False,
            "error": getattr(exc, "message", str(exc))[:2000],
            "error_code": getattr(exc, "code", "engine_adapter_error"),
        }
        _audit_copilot_cli(db, job, actor_id, phase="failed", task_type=task_type, payload=fail_payload)
        db.flush()
        raise
    run_metadata = _build_cli_run_metadata(invocation, proc_meta, cwd=root)
    complete_payload = {**run_metadata, "success": True}
    _audit_copilot_cli(db, job, actor_id, phase="completed", task_type=task_type, payload=complete_payload)
    db.flush()
    return proc_meta, run_metadata


def _compose_prompt(request: EngineRequest, job: AutomationJob, *, mode: str) -> str:
    """Translate normalized :class:`EngineRequest` into a single Copilot CLI task string."""
    lines: list[str] = [
        "You are an expert test automation engineer. The repository is at the process working directory (cwd).",
        f"QSwarm task type: {request.task_type.value}",
        f"Adapter mode label: {mode}",
        "",
        "Implement the approved change plan by editing only the listed files on disk.",
        "Requirements:",
        "- Output valid source only in those files (no markdown fences in .ts files).",
        "- Do not add assistant-style prose inside source files.",
        "- Follow existing Playwright patterns in this repository when applicable.",
        "",
    ]
    if request.source_type or request.source_reference:
        lines.append(f"Source: type={request.source_type!r} reference={request.source_reference!r}")
    if request.automation_goal:
        lines.append(f"Automation goal / case title: {request.automation_goal}")
    if request.source_payload:
        lines.append("Source payload (structured):")
        lines.append(_safe_json(dict(request.source_payload)))
    if request.plan_context:
        lines.append("Change plan (authoritative):")
        lines.append(_safe_json(request.plan_context))
    if request.existing_context:
        lines.append("Framework / repo context (hints):")
        lines.append(_safe_json(request.existing_context))
    if request.patch_context and mode in ("revision", "initial_generation"):
        lines.append("Current / previous patch context (reference):")
        lines.append(_safe_json(request.patch_context))
    if request.execution_context:
        lines.append("Last execution context (reference):")
        lines.append(_safe_json(request.execution_context))
    if mode == "revision" and request.revision_instruction:
        lines.append("Revision instruction from reviewer:")
        lines.append(request.revision_instruction.strip())
    if request.target_scope:
        lines.append(f"Target scope hint: {request.target_scope.strip()}")
    return "\n".join(lines) + "\n"


def _safe_json(blob: dict[str, Any]) -> str:
    try:
        return json.dumps(blob, indent=2, default=str)[:120000]
    except Exception:
        return str(blob)[:120000]


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


def _changed_files_from_patch(patch: dict[str, Any] | None) -> list[str]:
    if not isinstance(patch, dict):
        return []
    gf = patch.get("generated_files")
    if not isinstance(gf, list):
        return []
    out: list[str] = []
    for x in gf:
        if isinstance(x, dict) and x.get("path"):
            out.append(str(x["path"]))
    return out


def _run_cli(
    settings: Settings,
    *,
    argv: list[str],
    cwd: Path,
) -> dict[str, Any]:
    if not cwd.is_dir():
        raise EngineRepoAccessError(f"workspace is not a directory: {cwd}", code="engine_repo_access")
    timeout = float(settings.qswarm_copilot_agent_timeout_seconds)
    result = run_subprocess_argv(argv, cwd=cwd, timeout_seconds=timeout)
    if result.get("timed_out"):
        raise EngineTimeoutError(
            f"Copilot CLI exceeded timeout ({settings.qswarm_copilot_agent_timeout_seconds}s)",
            code="engine_timeout",
        )
    code = result.get("exit_code")
    if code != 0:
        tail = (result.get("stderr") or result.get("stdout") or "")[-4000:]
        raise EngineAdapterError(
            f"Copilot CLI exited with code {code}: {tail}",
            code="engine_cli_nonzero",
        )
    return result


class CopilotAgentAdapter(CodingAgentAdapterBase):
    """Runs GitHub Copilot CLI in ``repo_path`` cwd; patch content is assembled from disk (honest I/O)."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings

    def _s(self) -> Settings:
        return self._settings if self._settings is not None else get_settings()

    @property
    def engine_name(self) -> str:
        return CodingEngineName.COPILOT_AGENT.value

    def get_capabilities(self) -> EngineCapability:
        s = self._s()
        if not s.qswarm_copilot_agent_enabled:
            configured = False
        else:
            try:
                configured = self.validate_config()
            except EngineConfigurationError:
                configured = False
        allow_rev = bool(s.qswarm_copilot_agent_allow_revision)
        return EngineCapability(
            engine_name=self.engine_name,
            display_name="GitHub Copilot (CLI)",
            configured=configured,
            supports_initial_request=True,
            supports_revision_request=allow_rev,
            supports_manual_rerun_request=True,
            supports_plan=True,
            supports_patch=True,
            supports_execution=True,
            supports_repo_context=True,
            supports_structured_output=False,
            supports_streaming=False,
            supports_pr_creation=False,
            notes=(
                "Local Copilot CLI subprocess in repo cwd; stdout/stderr are free-form. "
                "Patch bytes come from workspace files after the run for QSwarm validation. "
                "Manual rerun does not invoke the CLI (re-exec only). "
                "SDK/cloud integration is future work."
            ),
        )

    def validate_config(self) -> bool:
        s = self._s()
        if not s.qswarm_copilot_agent_enabled:
            return False
        if not (s.qswarm_copilot_agent_command or "").strip():
            raise EngineConfigurationError(
                "QSWARM_COPILOT_AGENT_COMMAND is empty while QSWARM_COPILOT_AGENT_ENABLED=true",
                code="engine_configuration",
            )
        _resolve_cli_executable(s)
        return True

    def _ensure_ready(self) -> None:
        if not self._s().qswarm_copilot_agent_enabled:
            raise EngineConfigurationError(
                "Copilot agent is disabled (QSWARM_COPILOT_AGENT_ENABLED=false).",
                code="engine_configuration",
            )
        self.validate_config()

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
        proc_meta, run_metadata = _invoke_copilot_cli(
            db,
            job,
            aid,
            s,
            root=root,
            prompt=prompt,
            task_type=EngineTaskType.INITIAL_GENERATION,
        )

        try:
            raw_patch = build_full_generation_patch_from_workspace(
                job, root, engine_run_label="copilot_agent"
            )
        except FileNotFoundError as e:
            raise EngineMalformedOutputError(
                f"Expected plan file missing on disk after Copilot run: {e}",
                code="engine_malformed_output",
            ) from e
        except ValueError as e:
            raise EngineMalformedOutputError(str(e), code="engine_malformed_output") from e

        automation_job_service.generate_code_from_external_patch(
            db, job.id, raw_patch, actor_id=aid, provider_label="copilot_agent"
        )
        db.refresh(job)
        automation_job_service.execute_automation_job(db, job.id, actor_id=aid)
        db.refresh(job)

        ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else {}
        cmd = ex.get("command")
        ok = bool(ex.get("success"))
        patch = job.generated_patch_json if isinstance(job.generated_patch_json, dict) else {}
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.INITIAL_GENERATION,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            plan_summary=_plan_summary(plan),
            patch_summary=_patch_summary(patch),
            changed_files=_changed_files_from_patch(patch),
            plan_payload=plan or None,
            patch_payload=dict(patch) if patch else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            raw_output=(proc_meta.get("stdout") or "")[-200000:] or None,
            error_code=None if ok else "execution_failed",
            error_message=None
            if ok
            else (job.blocked_reason or str((ex.get("notes") or [""])[0])),
            metadata=run_metadata,
        )

    def run_revision_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        self._ensure_ready()
        _assert_request_matches_context(request, context)
        s = self._s()
        if not s.qswarm_copilot_agent_allow_revision:
            raise EngineConfigurationError(
                "Copilot revisions are disabled (QSWARM_COPILOT_AGENT_ALLOW_REVISION=false).",
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
        proc_meta, run_metadata = _invoke_copilot_cli(
            db,
            job,
            aid,
            s,
            root=root,
            prompt=prompt,
            task_type=EngineTaskType.REVISION,
        )

        try:
            touched = paths_for_revision_scope(job, request.target_scope)
            raw_patch = build_repair_subset_patch_from_workspace(
                job, root, touched_paths=touched, engine_run_label="copilot_agent"
            )
        except FileNotFoundError as e:
            raise EngineMalformedOutputError(
                f"Expected file missing on disk after Copilot revision: {e}",
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
            provider_label="copilot_agent",
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
            changed_files=_changed_files_from_patch(patch),
            patch_payload=dict(patch) if patch else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            raw_output=(proc_meta.get("stdout") or "")[-200000:] or None,
            error_code=None if ok else "execution_failed",
            error_message=None if ok else (job.blocked_reason or str((ex.get("notes") or [""])[0])),
            metadata=run_metadata,
        )

    def run_manual_rerun_request(self, request: EngineRequest, *, context: CodeSessionContext) -> EngineResult:
        """Re-run Playwright only; Copilot CLI is not invoked (same pattern as Claude)."""
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
        patch = (
            context.job.generated_patch_json if isinstance(context.job.generated_patch_json, dict) else {}
        )
        return EngineResult(
            engine_name=self.engine_name,
            task_type=EngineTaskType.MANUAL_RERUN,
            status=EngineResultStatus.SUCCEEDED if ok else EngineResultStatus.FAILED,
            changed_files=_changed_files_from_patch(patch),
            patch_summary=_patch_summary(patch) if patch else None,
            patch_payload=dict(patch) if patch else None,
            execution_command=cmd if isinstance(cmd, (list, dict)) else None,
            execution_result=ex if ex else None,
            error_code=None if ok else "execution_failed",
            error_message=None if ok else (context.job.blocked_reason or str((ex.get("notes") or [""])[0])),
            metadata={
                "engine_note": "manual_rerun_no_cli",
                "manual_rerun": (
                    "Copilot CLI is not invoked; QSwarm re-executes tests against the current workspace."
                ),
            },
        )
