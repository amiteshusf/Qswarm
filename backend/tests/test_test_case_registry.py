"""Tests for test case registry and Sprint 1 → Sprint 2 bridge."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.connectors.jira_client as jira_mod
from app.core.config import get_settings
from app.core.constants import TestCaseAutomationStatus, TestCasePublicationStatus
from app.db.models.approval import Approval
from app.db.models.automation_session import AutomationSession
from app.db.models.test_case_record import TestCaseRecord
from app.db.session import get_db
from app.main import app
from app.services.test_case_registry_service import materialize_test_cases_from_approved_workflow


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


def _approved_workflow(client, db_session) -> tuple[uuid.UUID, Approval]:
    r = client.post(
        "/workflow/runs",
        json={"jira_issue_key": "QSW-900", "initiated_by": "qa"},
    )
    run_id = uuid.UUID(r.json()["id"])
    client.post(f"/workflow/runs/{run_id}/start")
    appr = db_session.execute(select(Approval).where(Approval.workflow_run_id == run_id)).scalar_one()
    ar = client.post(f"/approvals/{appr.id}/approve", json={"actor_id": "reviewer", "notes": "ok"})
    assert ar.status_code == 200, ar.text
    db_session.expire_all()
    return run_id, appr


def test_approval_materializes_test_case_registry(client, db_session):
    run_id, appr = _approved_workflow(client, db_session)
    rows = list(
        db_session.scalars(select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == run_id)).all()
    )
    assert len(rows) >= 1
    row = rows[0]
    assert row.source_story_key == "QSW-900"
    assert row.approval_status == "approved"
    assert row.registry_key.startswith("QSW-900-TC-")


def test_materialize_is_idempotent(db_session):
    from app.db.models.agent_artifact import AgentArtifact
    from app.db.models.approval import Approval
    from app.db.models.workflow_run import WorkflowRun
    from app.core.constants import ArtifactType

    run = WorkflowRun(workflow_name="sprint1", initiated_by="qa", graph_state_json={"jira_issue_key": "QSW-IDEM"})
    db_session.add(run)
    db_session.flush()
    art = AgentArtifact(
        workflow_run_id=run.id,
        agent_name="test_design_agent",
        artifact_type=ArtifactType.TEST_DESIGN.value,
        version=1,
        content_json={
            "scenario_set": [
                {
                    "title": "Login works",
                    "type": "positive",
                    "steps_outline": ["Open app", "Login"],
                    "expected_results": ["Dashboard visible"],
                }
            ]
        },
    )
    db_session.add(art)
    db_session.flush()
    appr = Approval(
        workflow_run_id=run.id,
        artifact_id=art.id,
        status="approved",
        requested_by="qa",
    )
    db_session.add(appr)
    db_session.commit()

    first = materialize_test_cases_from_approved_workflow(
        db_session, appr, actor_id="qa", auto_publish=False
    )
    second = materialize_test_cases_from_approved_workflow(
        db_session, appr, actor_id="qa", auto_publish=False
    )
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id


def test_publish_marks_automation_ready(client, db_session):
    run_id, _appr = _approved_workflow(client, db_session)
    row = db_session.scalars(
        select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == run_id)
    ).first()
    assert row is not None
    if row.publication_status != TestCasePublicationStatus.PUBLISHED.value:
        pr = client.post(f"/api/v1/test-cases/{row.id}/publish?actor_id=qa")
        assert pr.status_code == 200, pr.text
        db_session.expire_all()
        row = db_session.get(TestCaseRecord, row.id)
    assert row.publication_status == TestCasePublicationStatus.PUBLISHED.value
    assert row.external_id
    assert row.automation_status == TestCaseAutomationStatus.AUTOMATION_READY.value


def test_list_automation_ready_test_cases(ui_client, client, db_session, tmp_path):
    run_id, _appr = _approved_workflow(client, db_session)
    row = db_session.scalars(
        select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == run_id)
    ).first()
    assert row is not None
    if row.publication_status != TestCasePublicationStatus.PUBLISHED.value:
        client.post(f"/api/v1/test-cases/{row.id}/publish?actor_id=qa")
        db_session.expire_all()

    lst = ui_client.get("/api/v1/test-cases?status=automation_ready")
    assert lst.status_code == 200, lst.text
    items = lst.json()["items"]
    assert any(x["id"] == str(row.id) for x in items)


def test_automate_test_case_creates_session_with_traceability(ui_client, client, db_session, tmp_path):
    run_id, _appr = _approved_workflow(client, db_session)
    row = db_session.scalars(
        select(TestCaseRecord).where(TestCaseRecord.workflow_run_id == run_id)
    ).first()
    assert row is not None
    if row.publication_status != TestCasePublicationStatus.PUBLISHED.value:
        client.post(f"/api/v1/test-cases/{row.id}/publish?actor_id=qa")
        db_session.expire_all()
        row = db_session.get(TestCaseRecord, row.id)

    auto = ui_client.post(
        f"/api/v1/test-cases/{row.id}/automate",
        json={
            "createdBy": "qa",
            "engine": "stub",
            "repoPath": str(tmp_path.resolve()),
        },
    )
    assert auto.status_code == 200, auto.text
    body = auto.json()
    sid = body["id"]
    sess = db_session.get(AutomationSession, uuid.UUID(sid))
    assert sess is not None
    assert sess.test_case_record_id == row.id
    assert sess.workflow_run_id == run_id
    assert sess.source_system == "jira"
    assert sess.source_reference == "QSW-900"
    assert sess.approved_case_id == row.external_id

    db_session.refresh(row)
    assert row.automation_session_id == sess.id
    assert row.automation_status == TestCaseAutomationStatus.IN_PROGRESS.value

    brief = ui_client.get(f"/api/v1/sessions/{sid}/brief")
    assert brief.status_code == 200
    src = brief.json()["sourceSummary"]
    assert src.get("sourceStoryKey") == "QSW-900"
    assert src.get("publishedTestCaseId") == row.external_id


def test_direct_session_create_still_works(ui_client, tmp_path):
    c = ui_client.post(
        "/api/v1/sessions",
        json={
            "approvedCaseId": "LEGACY-1",
            "engine": "stub",
            "createdBy": "qa",
            "repoPath": str(tmp_path.resolve()),
            "steps": ["x"],
        },
    )
    assert c.status_code == 201, c.text
