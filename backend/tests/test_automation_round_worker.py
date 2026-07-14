"""Background automation round worker tests."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.constants import AutomationJobStatus, AutomationRevisionRoundStatus
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.services.automation_round_worker_service import run_worker_once
from test_automation_jobs import _patch_playwright_run_for_job_and_review, _stub_execution_run_factory
from test_automation_sessions import _create_session


def test_start_returns_202_when_worker_async(client, tmp_path: Path, monkeypatch, db_session):
    monkeypatch.setenv("QSWARM_AUTOMATION_RUN_WORKER_INLINE", "false")
    get_settings.cache_clear()
    data = _create_session(client, tmp_path, case_id="ASYNC-START")
    sid = uuid.UUID(data["id"])
    st = client.post(f"/automation/sessions/{sid}/start", json={})
    assert st.status_code == 202, st.text
    body = st.json()
    assert body["accepted_async"] is True
    assert body["job_status"] == AutomationJobStatus.QUEUED.value

    rnd = db_session.scalar(
        select(AutomationRevisionRound).where(AutomationRevisionRound.automation_session_id == sid)
    )
    assert rnd is not None
    assert rnd.status == AutomationRevisionRoundStatus.QUEUED.value


def test_worker_executes_queued_start_round(client, tmp_path: Path, monkeypatch, db_session):
    monkeypatch.setenv("QSWARM_AUTOMATION_RUN_WORKER_INLINE", "false")
    get_settings.cache_clear()
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())

    data = _create_session(client, tmp_path, case_id="WORKER-START")
    sid = uuid.UUID(data["id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 202

    assert run_worker_once(db_session) is True
    db_session.commit()

    sess = db_session.get(AutomationSession, sid)
    job = db_session.get(AutomationJob, sess.automation_job_id)
    rnd = db_session.scalar(
        select(AutomationRevisionRound).where(AutomationRevisionRound.automation_session_id == sid)
    )
    assert rnd.status == AutomationRevisionRoundStatus.COMPLETED.value
    assert sess.current_round_number == 1
    assert job.status == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value


def test_revision_returns_202_when_worker_async(client, tmp_path: Path, monkeypatch, db_session):
    monkeypatch.setenv("QSWARM_AUTOMATION_RUN_WORKER_INLINE", "false")
    get_settings.cache_clear()
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())

    data = _create_session(client, tmp_path, case_id="ASYNC-REV")
    sid = uuid.UUID(data["id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 202
    assert run_worker_once(db_session) is True
    db_session.commit()

    rv = client.post(
        f"/automation/sessions/{sid}/request-revision",
        json={"actor_id": "qa", "instruction_text": "tighten locator"},
    )
    assert rv.status_code == 202, rv.text
    assert rv.json()["accepted_async"] is True

    rounds = db_session.scalars(
        select(AutomationRevisionRound)
        .where(AutomationRevisionRound.automation_session_id == sid)
        .order_by(AutomationRevisionRound.round_number)
    ).all()
    assert len(rounds) == 2
    assert rounds[1].status == AutomationRevisionRoundStatus.QUEUED.value
