"""QSwarm-first Sprint 1 test-design workspace API tests."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.connectors.jira_client as jira_mod
from app.core.config import get_settings
from app.core.constants import WorkflowRunStatus
from app.db.models.approval import Approval
from app.db.models.automation_session import AutomationSession
from app.db.models.workflow_run import WorkflowRun
from app.db.session import get_db
from app.main import app


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


def _create_workspace_run(ui_client, story_key: str = "QSW-WS-1") -> str:
    r = ui_client.post(
        f"/api/v1/stories/{story_key}/test-design-runs",
        json={"initiatedBy": "qa-lead"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["currentStage"] == "intake_ready"
    return body["id"]


def _through_plan_approval(ui_client, run_id: str) -> None:
    ar = ui_client.post(f"/api/v1/test-design-runs/{run_id}/analyze")
    assert ar.status_code == 200, ar.text
    assert ar.json()["content"]["storyKey"]

    pr = ui_client.post(f"/api/v1/test-design-runs/{run_id}/prepare-plan")
    assert pr.status_code == 200, pr.text
    assert pr.json()["content"]["functionalAreas"]

    ap = ui_client.post(f"/api/v1/test-design-runs/{run_id}/approve-plan")
    assert ap.status_code == 200, ap.text
    assert ap.json()["currentStage"] == "plan_approved"


def _through_test_case_review(ui_client, run_id: str) -> dict:
    _through_plan_approval(ui_client, run_id)
    gen = ui_client.post(f"/api/v1/test-design-runs/{run_id}/generate-test-cases")
    assert gen.status_code == 200, gen.text
    assert gen.json()["reviewSummary"]["testCaseCount"] >= 1
    return gen.json()


def test_list_eligible_jira_stories(ui_client):
    r = ui_client.get("/api/v1/stories?projectKey=QSW")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    assert "storyKey" in items[0]
    assert "readiness" in items[0]


def test_get_story_detail(ui_client):
    r = ui_client.get("/api/v1/stories/QSW-WS-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["storyKey"] == "QSW-WS-1"
    assert body["title"]


def test_create_workspace_run_from_story(ui_client, db_session):
    run_id = _create_workspace_run(ui_client)
    run = db_session.get(WorkflowRun, uuid.UUID(run_id))
    assert run is not None
    assert run.workflow_name == "sprint1_qswarm_workspace"
    assert run.graph_state_json["product_workspace"]["mode"] == "qswarm_first"


def test_active_run_conflict(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-2")
    assert run_id
    dup = ui_client.post(
        "/api/v1/stories/QSW-WS-2/test-design-runs",
        json={"initiatedBy": "qa-lead"},
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "active_run_exists"


def test_requirement_analysis_generation(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-3")
    r = ui_client.post(f"/api/v1/test-design-runs/{run_id}/analyze")
    assert r.status_code == 200, r.text
    content = r.json()["content"]
    assert content["acceptanceCriteria"]
    assert "readiness" in content

    get_r = ui_client.get(f"/api/v1/test-design-runs/{run_id}/analysis")
    assert get_r.status_code == 200
    assert get_r.json()["version"] == 1


def test_plan_preparation_and_approval(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-4")
    _through_plan_approval(ui_client, run_id)

    plan = ui_client.get(f"/api/v1/test-design-runs/{run_id}/plan")
    assert plan.status_code == 200
    assert plan.json()["planApproved"] is True


def test_generation_blocked_before_plan_approval(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-5")
    ui_client.post(f"/api/v1/test-design-runs/{run_id}/analyze")
    gen = ui_client.post(f"/api/v1/test-design-runs/{run_id}/generate-test-cases")
    assert gen.status_code == 409
    assert gen.json()["detail"]["code"] == "plan_not_approved"


def test_plan_revision_creates_new_version(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-6")
    ui_client.post(f"/api/v1/test-design-runs/{run_id}/analyze")
    ui_client.post(f"/api/v1/test-design-runs/{run_id}/prepare-plan")

    rev = ui_client.post(
        f"/api/v1/test-design-runs/{run_id}/request-plan-revision",
        json={"instruction": "Add more boundary coverage for checkout totals"},
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["currentStage"] == "plan_revision_requested"

    ui_client.post(f"/api/v1/test-design-runs/{run_id}/prepare-plan")
    plan = ui_client.get(f"/api/v1/test-design-runs/{run_id}/plan")
    assert plan.json()["version"] == 2


def test_structured_test_case_generation(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-7")
    review = _through_test_case_review(ui_client, run_id)
    assert review["testCases"][0]["registryKey"].startswith("QSW-WS-7-TC-")
    assert review["testCases"][0]["steps"]


def test_workspace_revision_creates_new_version(ui_client, db_session):
    from app.db.models.test_design_version import TestDesignVersion

    run_id = _create_workspace_run(ui_client, "QSW-WS-8")
    _through_test_case_review(ui_client, run_id)

    rev = ui_client.post(
        f"/api/v1/test-design-runs/{run_id}/request-revision",
        json={"instruction": "Add negative checkout coverage", "action": "refine"},
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["newVersionNumber"] == 2

    db_session.expire_all()
    versions = list(
        db_session.scalars(
            select(TestDesignVersion).where(TestDesignVersion.workflow_run_id == uuid.UUID(run_id))
        ).all()
    )
    assert len(versions) == 2


def test_jira_comment_revision_still_works(client, db_session):
    """Legacy Sprint 1 Jira happy path remains intact."""
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-LEG-1", "initiated_by": "tester"},
    )
    run_id = r.json()["id"]
    client.post(f"/workflow/runs/{run_id}/start")
    assert client.get(f"/workflow/runs/{run_id}").json()["status"] == WorkflowRunStatus.AWAITING_APPROVAL.value


def test_stale_version_cannot_be_approved(ui_client, db_session):
    run_id = _create_workspace_run(ui_client, "QSW-WS-9")
    _through_test_case_review(ui_client, run_id)

    appr = db_session.scalars(
        select(Approval).where(
            Approval.workflow_run_id == uuid.UUID(run_id),
            Approval.status == "pending",
        )
    ).one()
    stale_artifact_id = appr.artifact_id

    ui_client.post(
        f"/api/v1/test-design-runs/{run_id}/request-revision",
        json={"instruction": "Improve expected results"},
    )

    db_session.expire_all()
    appr = db_session.scalars(
        select(Approval).where(
            Approval.workflow_run_id == uuid.UUID(run_id),
            Approval.status == "pending",
        )
    ).one()
    appr.artifact_id = stale_artifact_id
    db_session.flush()

    bad = ui_client.post(
        f"/api/v1/test-design-runs/{run_id}/approve",
        json={"actorId": "reviewer", "notes": "ok"},
    )
    assert bad.status_code == 409
    assert bad.json()["detail"]["code"] == "stale_version_not_approvable"


def test_approval_materializes_registry_rows(ui_client, db_session):
    from app.db.models.test_case_record import TestCaseRecord

    run_id = _create_workspace_run(ui_client, "QSW-WS-10")
    _through_test_case_review(ui_client, run_id)

    appr = ui_client.post(
        f"/api/v1/test-design-runs/{run_id}/approve",
        json={"actorId": "reviewer", "notes": "approved"},
    )
    assert appr.status_code == 200, appr.text
    assert appr.json()["currentStage"] == "approved"

    db_session.expire_all()
    rows = list(
        db_session.scalars(select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == uuid.UUID(run_id))).all()
    )
    assert len(rows) >= 1
    assert rows[0].approval_status == "approved"


def test_publication_stores_external_ids(ui_client, db_session):
    from app.core.constants import TestCasePublicationStatus
    from app.db.models.test_case_record import TestCaseRecord

    run_id = _create_workspace_run(ui_client, "QSW-WS-11")
    _through_test_case_review(ui_client, run_id)
    ui_client.post(
        f"/api/v1/test-design-runs/{run_id}/approve",
        json={"actorId": "reviewer"},
    )

    db_session.expire_all()
    row = db_session.scalars(
        select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == uuid.UUID(run_id))
    ).first()
    if row and row.publication_status != TestCasePublicationStatus.PUBLISHED.value:
        pub = ui_client.post(f"/api/v1/test-design-runs/{run_id}/publish")
        assert pub.status_code == 200, pub.text

    db_session.expire_all()
    row = db_session.scalars(
        select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == uuid.UUID(run_id))
    ).first()
    assert row is not None
    assert row.external_id
    assert row.publication_status == TestCasePublicationStatus.PUBLISHED.value


def test_automation_ready_backlog_and_sprint2_handoff(ui_client, db_session, tmp_path):
    from app.core.constants import TestCasePublicationStatus
    from app.db.models.test_case_record import TestCaseRecord

    run_id = _create_workspace_run(ui_client, "QSW-WS-12")
    _through_test_case_review(ui_client, run_id)
    ui_client.post(f"/api/v1/test-design-runs/{run_id}/approve", json={"actorId": "reviewer"})

    db_session.expire_all()
    row = db_session.scalars(
        select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == uuid.UUID(run_id))
    ).first()
    if row.publication_status != TestCasePublicationStatus.PUBLISHED.value:
        ui_client.post(f"/api/v1/test-cases/{row.id}/publish?actor_id=qa")

    lst = ui_client.get("/api/v1/test-cases?status=automation_ready")
    assert lst.status_code == 200
    assert any(x["id"] == str(row.id) for x in lst.json()["items"])

    auto = ui_client.post(
        f"/api/v1/test-cases/{row.id}/automate",
        json={
            "createdBy": "qa",
            "engine": "stub",
            "repoPath": str(tmp_path.resolve()),
        },
    )
    assert auto.status_code == 200, auto.text
    session_id = auto.json()["id"]
    sess = db_session.get(AutomationSession, uuid.UUID(session_id))
    assert sess is not None
    assert sess.test_case_record_id == row.id


def test_run_detail_traceability(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-13")
    _through_plan_approval(ui_client, run_id)

    detail = ui_client.get(f"/api/v1/test-design-runs/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["requirementAnalysis"]
    assert body["testDesignPlan"]
    assert body["nextActions"] == ["generate_test_cases"]
    assert body["sourceStory"]["storyKey"] == "QSW-WS-13"


def test_review_data_payload(ui_client):
    run_id = _create_workspace_run(ui_client, "QSW-WS-14")
    _through_test_case_review(ui_client, run_id)

    rd = ui_client.get(f"/api/v1/test-design-runs/{run_id}/review-data")
    assert rd.status_code == 200
    body = rd.json()
    assert body["reviewSummary"]["testCaseCount"] >= 1
    assert body["versions"]
    assert body["conversation"] is not None
