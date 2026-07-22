"""API schemas for test case registry / automation backlog."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class TestCaseAutomateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    created_by: str = Field(..., min_length=1, max_length=256)
    coding_engine: str = Field(default="stub", max_length=64)
    repository_connection_id: uuid.UUID | None = None
    repo_path: str | None = Field(default=None, max_length=1024)
    base_branch: str = Field(default="main", max_length=256)


class UiTestCaseAutomate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    created_by: str = Field(default="qswarm-web", alias="createdBy", min_length=1, max_length=256)
    engine: str = Field(default="stub", max_length=64)
    repository_connection_id: uuid.UUID | None = Field(default=None, alias="repositoryConnectionId")
    repo_path: str | None = Field(default=None, alias="repoPath", max_length=1024)
    base_branch: str = Field(default="main", alias="baseBranch", max_length=256)

    def to_legacy(self) -> TestCaseAutomateRequest:
        return TestCaseAutomateRequest(
            created_by=self.created_by,
            coding_engine=self.engine,
            repository_connection_id=self.repository_connection_id,
            repo_path=self.repo_path,
            base_branch=self.base_branch,
        )
