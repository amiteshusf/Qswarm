"""Jira-related API schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class JiraConnectionTestResponse(BaseModel):
    """Result of ``GET /jira/connection-test`` (no secrets)."""

    ok: bool
    mode: Literal["stub", "real"]
    message: str
    base_url: str | None = None
    sample_issue_key: str | None = None
    error: str | None = None


class JiraIssueFetchResponse(BaseModel):
    """Normalized issue for ``GET /jira/issues/{issue_key}`` (no raw Jira payload)."""

    ok: bool = True
    issue_key: str
    summary: str | None = None
    description: str | None = None
    status: str | None = None
    issue_type: str | None = None
    assignee: str | None = None
    reporter: str | None = None
    priority: str | None = None


class JiraStoryResponse(BaseModel):
    issue_key: str
    issue_id: str | None = None
    summary: str
    description: str | None = None
    issue_type: str | None = None
    priority: str | None = None
    status: str | None = None
    assignee: str | None = None
    reporter: str | None = None
    labels: list[str] = Field(default_factory=list)
    raw_available: bool = True


class JiraSearchRequest(BaseModel):
    jql: str = Field(..., min_length=1, examples=["project = QSW ORDER BY created DESC"])
    max_results: int = Field(default=20, ge=1, le=100)


class JiraSearchHit(BaseModel):
    issue_key: str
    summary: str
    status: str | None = None


class JiraSearchResponse(BaseModel):
    issues: list[JiraSearchHit]
    total: int | None = None
