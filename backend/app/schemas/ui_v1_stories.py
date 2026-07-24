"""UI contract schemas for Jira story intake (GET /api/v1/stories)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StoryReadiness = Literal["ready", "partial", "missing_ac"]
AcceptanceCriteriaStatus = Literal["ready", "partial", "missing_ac"]


class UiStorySummary(BaseModel):
    """Canonical story row for QSwarm Web story picker."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    story_key: str = Field(..., alias="storyKey")
    title: str
    description: str = ""
    status: str | None = None
    sprint: str | None = None
    project_key: str = Field(..., alias="projectKey")
    assignee: str | None = None
    readiness: StoryReadiness
    acceptance_criteria_status: AcceptanceCriteriaStatus = Field(..., alias="acceptanceCriteriaStatus")
    missing_information: list[str] = Field(default_factory=list, alias="missingInformation")
    has_active_run: bool = Field(default=False, alias="hasActiveRun")
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    jira_url: str | None = Field(default=None, alias="jiraUrl")


class UiStoryListResponse(BaseModel):
    """Root response for GET /api/v1/stories."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    stories: list[UiStorySummary]
    total: int
