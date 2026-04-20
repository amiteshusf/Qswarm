"""Story intake artifact schemas."""

from typing import Any

from pydantic import BaseModel, Field


class TestableCriterion(BaseModel):
    text: str
    source: str = Field(default="inferred_from_description")


class StoryIntakeArtifactContent(BaseModel):
    story_key: str
    business_goal: str
    in_scope: list[str]
    out_of_scope: list[str]
    assumptions: list[str]
    risks: list[str]
    open_questions: list[str]
    testable_acceptance_criteria: list[TestableCriterion]
    recommended_test_focus: list[str]


class IntakeFromJiraResponse(BaseModel):
    issue_key: str
    intake: StoryIntakeArtifactContent
