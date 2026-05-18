"""Shared types for pluggable coding agents (Sprint 2 control plane)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models.automation_job import AutomationJob
    from app.db.models.automation_revision_round import AutomationRevisionRound
    from app.db.models.automation_session import AutomationSession


@dataclass
class CodeSessionContext:
    """Inputs passed to a coding-engine adapter for one orchestration step."""

    db: "Session"
    session: "AutomationSession"
    job: "AutomationJob"
    actor_id: str
    revision_round: "AutomationRevisionRound"


@dataclass
class PlanResult:
    plan_json: dict[str, Any]


@dataclass
class PatchResult:
    """Persisted patch summary shape (matches ``generated_patch_json`` on the job)."""

    patch_json: dict[str, Any]
