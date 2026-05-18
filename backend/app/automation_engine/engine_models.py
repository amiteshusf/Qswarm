"""Normalized, engine-agnostic request/result models for coding adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EngineTaskType(StrEnum):
    INITIAL_GENERATION = "initial_generation"
    REVISION = "revision"
    MANUAL_RERUN = "manual_rerun"


class EngineResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REQUIRES_CONFIGURATION = "requires_configuration"
    TIMED_OUT = "timed_out"
    MALFORMED_OUTPUT = "malformed_output"


class EngineCapability(BaseModel):
    """Describes what an engine adapter can do (Milestone 1 metadata; no network)."""

    engine_name: str
    display_name: str
    configured: bool = False
    supports_initial_request: bool = True
    supports_revision_request: bool = True
    supports_manual_rerun_request: bool = True
    supports_plan: bool = True
    supports_patch: bool = True
    supports_execution: bool = True
    supports_repo_context: bool = True
    supports_structured_output: bool = True
    supports_streaming: bool = False
    supports_pr_creation: bool = False
    notes: str | None = None


class EngineRequest(BaseModel):
    """Normalized input for a single adapter invocation (no Claude/Copilot-specific shape)."""

    session_id: str
    job_id: str
    round_id: str
    engine_name: str
    task_type: EngineTaskType
    source_type: str | None = None
    source_reference: str | None = None
    source_payload: dict[str, Any] = Field(default_factory=dict)
    repo_url: str | None = None
    repo_path: str | None = None
    target_branch: str | None = None
    workspace_path: str | None = None
    automation_goal: str | None = None
    test_framework: str | None = None
    language: str | None = None
    existing_context: dict[str, Any] = Field(default_factory=dict)
    plan_context: dict[str, Any] | None = None
    patch_context: dict[str, Any] | None = None
    execution_context: dict[str, Any] | None = None
    revision_instruction: str | None = None
    target_scope: str | None = None
    requested_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EngineResult(BaseModel):
    """Normalized output from an adapter (success or structured failure)."""

    engine_name: str
    task_type: EngineTaskType
    status: EngineResultStatus
    plan_summary: str | None = None
    plan_payload: dict[str, Any] | None = None
    changed_files: list[str] = Field(default_factory=list)
    patch_summary: str | None = None
    patch_payload: dict[str, Any] | None = None
    execution_command: list[str] | dict[str, Any] | None = None
    execution_result: dict[str, Any] | None = None
    raw_output: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
