"""Orchestrate stub/LLM patch generation, validation, and workspace apply."""

from __future__ import annotations

from typing import Any

from app.db.models.automation_job import AutomationJob
from app.providers.coding.base import CodeIntelligenceProvider
from app.providers.coding.registry import get_coding_provider
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path
from app.services.generation_prompt_service import build_generation_payload
from app.services.patch_validation_service import (
    PatchValidationError,
    summarize_patch_for_persistence,
    validate_generated_patch,
)
from app.services.workspace_service import WorkspaceApplyError, apply_generated_patch


def run_code_generation_and_apply(
    job: AutomationJob,
    *,
    provider: CodeIntelligenceProvider | None = None,
) -> dict[str, Any]:
    """
    Build payload, generate patch, validate, apply to ``repo_path``, return persistence blob.

    Raises:
        PatchValidationError: plan scope / shape violations.
        FrameworkScanError: invalid ``repo_path`` before apply.
        WorkspaceApplyError: filesystem apply failure.
    """
    payload = build_generation_payload(job)
    p = provider or get_coding_provider()
    raw_patch = p.generate_patch(payload)
    validate_generated_patch(raw_patch, job)
    root = resolve_repo_path(job.repo_path)
    apply_result = apply_generated_patch(root, raw_patch["generated_files"])
    summary = summarize_patch_for_persistence(raw_patch)
    summary["apply_result"] = apply_result
    summary["provider"] = p.name
    return summary


def validate_apply_and_summarize_generated_patch(
    job: AutomationJob,
    raw_patch: dict[str, Any],
    *,
    provider_name: str,
) -> dict[str, Any]:
    """
    Validate a full-generation patch, apply to ``repo_path``, return persistence summary.

    Used by external engines (e.g. Claude Code CLI) that write files in the workspace first.
    """
    validate_generated_patch(raw_patch, job)
    root = resolve_repo_path(job.repo_path)
    apply_result = apply_generated_patch(root, raw_patch["generated_files"])
    summary = summarize_patch_for_persistence(raw_patch)
    summary["apply_result"] = apply_result
    summary["provider"] = provider_name
    return summary
