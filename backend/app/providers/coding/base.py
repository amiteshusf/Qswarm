"""Abstract coding intelligence provider (Codex, Claude, stub, …)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CodeIntelligenceProvider(ABC):
    """Pluggable provider for change planning and patch generation."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier for logs and audit."""

    @abstractmethod
    def create_change_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Produce a structured change plan from the assembled planning payload.

        The payload is built by ``planning_prompt_service`` and includes
        ``framework_summary``, ``case_spec``, ``repo_context``, ``planning_constraints``.
        """

    @abstractmethod
    def generate_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Produce a structured patch (full file contents) from the generation payload.

        Payload includes ``change_plan``, context blobs, and ``generation_constraints``.
        Implementations must only touch paths allowed by the approved change plan.
        """

    @abstractmethod
    def suggest_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Propose a repair patch after a failed execution.

        Payload includes failure analysis, execution summary, and ``repair_constraints``.
        Return a normal patch dict (``generated_files``, etc.) or ``{"skipped": true, ...}``.
        """

    @abstractmethod
    def revise_after_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Propose a patch from human review instructions (plain English).

        Payload includes ``reviewer_instruction``, ``revision_constraints``, and job blobs.
        Return a normal patch dict or ``{"skipped": true, "reason": "..."}``.
        """
