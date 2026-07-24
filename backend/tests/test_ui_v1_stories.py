"""Contract tests for GET /api/v1/stories."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.connectors.jira_client as jira_mod
from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.schemas.ui_v1_stories import UiStoryListResponse, UiStorySummary
from app.services.ui_v1_stories import (
    _compute_readiness_signals,
    build_story_list_for_ui,
    map_issue_to_ui_story,
    normalize_assignee,
    project_key_from_story,
)


@pytest.fixture(autouse=True)
def _reset_stub_jira():
    jira_mod._STUB_CREATE_SEQ[0] = 0
    jira_mod._STUB_COMMENT_SEQ[0] = 0
    jira_mod._STUB_COMMENTS_BY_ISSUE.clear()
    yield


@pytest.fixture
def ui_client(db_session):
    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_list_response_wrapper(ui_client):
    r = ui_client.get("/api/v1/stories?projectKey=QSW")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "stories" in body
    assert "items" not in body
    assert "total" in body
    assert isinstance(body["stories"], list)
    assert body["total"] == len(body["stories"])


def test_complete_story_response_shape(ui_client, monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://usfoods.atlassian.net")
    get_settings.cache_clear()

    r = ui_client.get("/api/v1/stories?projectKey=NSP")
    assert r.status_code == 200, r.text
    story = r.json()["stories"][0]
    expected_keys = {
        "storyKey",
        "title",
        "description",
        "status",
        "sprint",
        "projectKey",
        "assignee",
        "readiness",
        "acceptanceCriteriaStatus",
        "missingInformation",
        "hasActiveRun",
        "activeRunId",
        "jiraUrl",
    }
    assert expected_keys.issubset(story.keys())
    assert story["description"] == "" or isinstance(story["description"], str)
    assert story["missingInformation"] == [] or isinstance(story["missingInformation"], list)
    assert story["hasActiveRun"] is False
    assert story["activeRunId"] is None
    assert story["sprint"] is None or isinstance(story["sprint"], str)
    assert story["assignee"] is None or isinstance(story["assignee"], str)
    assert story["readiness"] in {"ready", "partial", "missing_ac"}
    assert story["acceptanceCriteriaStatus"] in {"ready", "partial", "missing_ac"}
    if story["storyKey"]:
        assert story["jiraUrl"] == f"https://usfoods.atlassian.net/browse/{story['storyKey']}"


def test_null_sprint_and_assignee(ui_client):
    r = ui_client.get("/api/v1/stories?projectKey=QSW")
    story = r.json()["stories"][0]
    assert story["sprint"] is None
    assert story["assignee"] is None


def test_story_with_active_run(ui_client):
    create = ui_client.post(
        "/api/v1/stories/QSW-ACTIVE/test-design-runs",
        json={"initiatedBy": "qa"},
    )
    assert create.status_code == 200, create.text
    run_id = create.json()["id"]

    listed = ui_client.get("/api/v1/stories?q=QSW-ACTIVE")
    assert listed.status_code == 200
    match = next((s for s in listed.json()["stories"] if s["storyKey"] == "QSW-ACTIVE"), None)
    assert match is not None
    assert match["hasActiveRun"] is True
    assert match["activeRunId"] == run_id


def test_readiness_values_mapper():
    ready_r, ready_ac, _ = _compute_readiness_signals(
        summary="User can add products to cart",
        description="Acceptance criteria:\n- User can add item\n- Cart count updates",
    )
    assert ready_r == "ready"
    assert ready_ac == "ready"

    partial_r, partial_ac, missing = _compute_readiness_signals(
        summary="Add products to cart",
        description="",
    )
    assert partial_r in {"partial", "missing_ac"}
    assert partial_ac in {"partial", "missing_ac"}
    assert missing

    miss_r, miss_ac, miss_info = _compute_readiness_signals(summary="", description="")
    assert miss_r == "missing_ac"
    assert miss_ac == "missing_ac"
    assert miss_info


def test_empty_story_list_total(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.test_design_workspace_service.list_stories_for_ui",
        lambda *args, **kwargs: {"items": [], "total": 0},
    )
    from app.connectors.jira_client import JiraClient

    result = build_story_list_for_ui(db_session, JiraClient(get_settings()), get_settings())
    assert result.stories == []
    assert result.total == 0


def test_pydantic_response_serializes_camel_case():
    story = UiStorySummary(
        story_key="NSP-696",
        title="Add products",
        description="As a shopper...",
        status="To Do",
        sprint=None,
        project_key="NSP",
        assignee=None,
        readiness="ready",
        acceptance_criteria_status="ready",
        missing_information=[],
        has_active_run=False,
        active_run_id=None,
        jira_url="https://usfoods.atlassian.net/browse/NSP-696",
    )
    payload = UiStoryListResponse(stories=[story], total=1).model_dump(by_alias=True)
    assert payload == {
        "stories": [
            {
                "storyKey": "NSP-696",
                "title": "Add products",
                "description": "As a shopper...",
                "status": "To Do",
                "sprint": None,
                "projectKey": "NSP",
                "assignee": None,
                "readiness": "ready",
                "acceptanceCriteriaStatus": "ready",
                "missingInformation": [],
                "hasActiveRun": False,
                "activeRunId": None,
                "jiraUrl": "https://usfoods.atlassian.net/browse/NSP-696",
            }
        ],
        "total": 1,
    }


def test_project_key_and_assignee_helpers():
    assert project_key_from_story("NSP-696") == "NSP"
    assert normalize_assignee("Unassigned") is None
    assert normalize_assignee("Jane Doe") == "Jane Doe"

    mapped = map_issue_to_ui_story(
        {
            "issue_key": "NSP-696",
            "summary": "Add products to cart from inventory page",
            "description": "As a shopper...",
            "status": "To Do",
            "sprint": None,
            "assignee": None,
        },
        settings=get_settings(),
        active_run_id=None,
    )
    assert mapped.story_key == "NSP-696"
    assert mapped.project_key == "NSP"
    assert mapped.has_active_run is False
