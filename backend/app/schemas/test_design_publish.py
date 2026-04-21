"""Canonical internal representation for publishing draft test designs (tool-neutral)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

CaseType = Literal["positive", "negative", "validation", "edge", "generic"]


class TestCaseDraft(BaseModel):
    """One draft test case derived from internal test design (publishable to Jira, TestRail, etc.)."""

    title: str = Field(..., min_length=1)
    case_type: CaseType = "generic"
    objective: str = ""
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class TestDesignPublishPackage(BaseModel):
    """Tool-neutral package produced before any publisher runs."""

    parent_issue_key: str = Field(..., min_length=1)
    workflow_run_id: uuid.UUID
    source_artifact_id: uuid.UUID
    cases: list[TestCaseDraft] = Field(default_factory=list)


class PublishResult(BaseModel):
    """Outcome of a publisher run (partial success allowed)."""

    success: bool = True
    """True when workflow may continue (including zero-case skip)."""
    hard_failure: bool = False
    """True when Sprint 1 should fail before approval."""
    created_issue_keys: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
