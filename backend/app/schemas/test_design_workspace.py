"""Pydantic schemas for QSwarm-first test-design workspace APIs."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class TestDesignRunCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    initiated_by: str = Field(..., min_length=1, max_length=256)


class UiTestDesignRunCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    initiated_by: str = Field(default="qswarm-web", alias="initiatedBy", min_length=1, max_length=256)


class WorkspaceRevisionBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor_id: str = Field(..., min_length=1, max_length=256)
    instruction: str = Field(..., min_length=1, max_length=20000)
    scope: str | None = Field(default=None, max_length=512)
    action: str = Field(default="refine", pattern="^(refine|regenerate)$")


class UiWorkspaceRevision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(default="qswarm-web", alias="actorId", max_length=256)
    instruction: str = Field(..., min_length=1, max_length=20000)
    scope: str | None = Field(default=None, max_length=512)
    action: str = Field(default="refine")

    def to_legacy(self) -> WorkspaceRevisionBody:
        return WorkspaceRevisionBody(
            actor_id=(self.actor_id or "qswarm-web").strip(),
            instruction=self.instruction,
            scope=self.scope,
            action=self.action if self.action in ("refine", "regenerate") else "refine",
        )


class WorkspaceApproveBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor_id: str = Field(..., min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=5000)


class UiWorkspaceApprove(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(default="qswarm-web", alias="actorId", min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=5000)


class WorkspacePlanRevisionBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    actor_id: str = Field(..., min_length=1, max_length=256)
    instruction: str = Field(..., min_length=1, max_length=20000)
    scope: str | None = Field(default=None, max_length=512)


class UiWorkspacePlanRevision(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    actor_id: str = Field(default="qswarm-web", alias="actorId", max_length=256)
    instruction: str = Field(..., min_length=1, max_length=20000)
    scope: str | None = Field(default=None, max_length=512)

    def to_legacy(self) -> WorkspacePlanRevisionBody:
        return WorkspacePlanRevisionBody(
            actor_id=(self.actor_id or "qswarm-web").strip(),
            instruction=self.instruction,
            scope=self.scope,
        )


class BulkTestDesignRunCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    story_keys: list[str] = Field(..., min_length=1, max_length=50)
    initiated_by: str = Field(..., min_length=1, max_length=256)


class UiBulkTestDesignRunCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    story_keys: list[str] = Field(..., alias="storyKeys", min_length=1, max_length=50)
    initiated_by: str = Field(default="qswarm-web", alias="initiatedBy", min_length=1, max_length=256)
