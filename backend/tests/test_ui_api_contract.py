"""Contract tests for documented /api/v1 UI API shapes."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.schemas.ui_v1_contract import (
    UiApiErrorResponse,
    UiStoryListResponse,
    UiTestCaseListResponse,
    UiTestCaseRecord,
    UiTestDesignReviewData,
    UiTestDesignRunDetail,
)

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[1] / "docs" / "api-fixtures"
OPENAPI_UI = Path(__file__).resolve().parents[1] / "docs" / "openapi-ui-v1.json"


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


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_openapi_ui_v1_export_exists_and_covers_routes():
    assert OPENAPI_UI.is_file(), "Run scripts/export_ui_api_fixtures.py to generate openapi-ui-v1.json"
    spec = json.loads(OPENAPI_UI.read_text(encoding="utf-8"))
    paths = spec.get("paths", {})
    assert "/api/v1/stories" in paths
    assert "/api/v1/test-design-runs/{run_id}" in paths
    assert "/api/v1/sessions/{session_id}/brief" in paths
    assert "/api/v1/test-cases" in paths


def test_fixture_stories_list_matches_schema():
    UiStoryListResponse.model_validate(_load_fixture("stories-list.json"))


@pytest.mark.parametrize(
    "fixture_name",
    [
        "test-design-run-intake-ready.json",
        "test-design-run-analysis-ready.json",
        "test-design-run-awaiting-plan-approval.json",
        "test-design-run-plan-approved.json",
        "test-design-run-awaiting-test-case-review.json",
        "test-design-run-approved.json",
        "test-design-run-automation-ready.json",
        "test-design-run-legacy.json",
    ],
)
def test_fixture_run_detail_stages_validate(fixture_name: str):
    payload = _load_fixture(fixture_name)
    # publish response may include publicationResult — strip for GET contract
    payload.pop("publicationResult", None)
    UiTestDesignRunDetail.model_validate(payload)


def test_fixture_review_data_validates():
    UiTestDesignReviewData.model_validate(_load_fixture("test-design-review-data.json"))


def test_fixture_test_case_list_validates():
    UiTestCaseListResponse.model_validate(_load_fixture("test-case-list.json"))


def test_fixture_test_case_detail_validates():
    UiTestCaseRecord.model_validate(_load_fixture("test-case-detail.json"))


def test_fixture_error_not_found_shape():
    UiApiErrorResponse.model_validate(_load_fixture("error-not-found.json"))


def test_fixture_error_invalid_state_shape():
    UiApiErrorResponse.model_validate(_load_fixture("error-invalid-state.json"))


def test_live_get_stories_matches_fixture_schema(ui_client):
    live = ui_client.get("/api/v1/stories?projectKey=NSP").json()
    UiStoryListResponse.model_validate(live)


def test_live_get_run_detail_matches_schema(ui_client):
    create = ui_client.post(
        "/api/v1/stories/NSP-LIVE/test-design-runs",
        json={"initiatedBy": "contract-test"},
    )
    assert create.status_code == 200, create.text
    run_id = create.json()["id"]
    live = ui_client.get(f"/api/v1/test-design-runs/{run_id}").json()
    UiTestDesignRunDetail.model_validate(live)


def test_live_run_detail_404_error_shape(ui_client):
    r = ui_client.get(f"/api/v1/test-design-runs/{uuid.uuid4()}")
    assert r.status_code == 404
    UiApiErrorResponse.model_validate(r.json())


def test_run_detail_nullable_fields_present(ui_client):
    create = ui_client.post(
        "/api/v1/stories/NSP-NULL/test-design-runs",
        json={"initiatedBy": "qa"},
    )
    body = ui_client.get(f"/api/v1/test-design-runs/{create.json()['id']}").json()
    assert "requirementAnalysis" in body and body["requirementAnalysis"] is None
    assert "testDesignPlan" in body and body["testDesignPlan"] is None
    assert "reviewIssue" in body and body["reviewIssue"] is None
    assert "blockedReason" in body
    assert body["testCaseRecords"] == []
    assert body["versions"] == []


def test_run_detail_next_actions_are_snake_case_tokens(ui_client):
    create = ui_client.post(
        "/api/v1/stories/NSP-ACTIONS/test-design-runs",
        json={"initiatedBy": "qa"},
    )
    body = ui_client.get(f"/api/v1/test-design-runs/{create.json()['id']}").json()
    assert body["nextActions"] == ["analyze_requirements"]


def test_legacy_run_detail_stage(ui_client, client):
    r = client.post("/workflow/runs", json={"jira_issue_key": "QSW-LEG-CON", "initiated_by": "t"})
    run_id = r.json()["id"]
    client.post(f"/workflow/runs/{run_id}/start")
    body = ui_client.get(f"/api/v1/test-design-runs/{run_id}").json()
    assert body["currentStage"] == "legacy_awaiting_approval"
    UiTestDesignRunDetail.model_validate(body)
