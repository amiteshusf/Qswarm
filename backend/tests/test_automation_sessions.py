"""Automation session control-plane (Sprint 2) tests."""

import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.core.constants import AuditEventType, AutomationJobStatus, AutomationSessionStatus
from app.db.models.audit_log import AuditLog
from app.db.models.automation_execution_attempt import AutomationExecutionAttempt
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_patch_version import AutomationPatchVersion
from app.db.models.automation_plan_version import AutomationPlanVersion
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.services.repository_connection_service import create_repository_connection
from app.services.repo_workspace_service import WorkspacePreparationResult
from test_automation_jobs import (
    _patch_playwright_run_for_job_and_review,
    _playwright_fixture_repo,
    _stub_execution_run_factory,
)
from test_repo_bootstrap_service import _fake_npm_run_populates_hosted_layout


def _create_session(client, tmp_path: Path, *, case_id: str = "SESS-001", source: str | None = "jira"):
    _playwright_fixture_repo(tmp_path)
    body = {
        "approved_case_id": case_id,
        "created_by": "runner",
        "coding_engine": "stub",
        "repo_path": str(tmp_path.resolve()),
        "case_title": "Smoke",
        "steps": ["open app"],
    }
    if source:
        body["source_system"] = source
        body["source_reference"] = "PROJ-1"
    r = client.post("/automation/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_session_create_links_job(client, tmp_path: Path, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-CREATE")
    sid = uuid.UUID(data["id"])
    jid = uuid.UUID(data["automation_job_id"])
    assert data["status"] == AutomationSessionStatus.PENDING.value
    job = db_session.get(AutomationJob, jid)
    assert job is not None
    assert job.approved_case_id == "SESS-CREATE"
    sess = db_session.get(AutomationSession, sid)
    assert sess is not None
    assert sess.automation_job_id == jid
    assert sess.coding_engine == "stub"


def test_session_start_records_round_plan_patch_execution(client, tmp_path: Path, monkeypatch, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-START")
    sid = uuid.UUID(data["id"])
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    st = client.post(f"/automation/sessions/{sid}/start", json={})
    assert st.status_code == 200, st.text
    assert st.json()["job_status"] == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value

    rounds = client.get(f"/automation/sessions/{sid}/rounds").json()["items"]
    assert len(rounds) == 1
    assert rounds[0]["round_number"] == 1
    assert rounds[0]["trigger_type"] == "initial"

    plans = client.get(f"/automation/sessions/{sid}/plan-versions").json()["items"]
    assert len(plans) == 1
    assert plans[0]["is_current"] is True
    assert plans[0]["plan_json"].get("framework_type") == "playwright"

    patches = client.get(f"/automation/sessions/{sid}/patch-versions").json()["items"]
    assert len(patches) == 1
    assert patches[0]["is_current"] is True
    assert patches[0]["patch_json"].get("provider") == "stub"

    attempts = client.get(f"/automation/sessions/{sid}/execution-attempts").json()["items"]
    assert len(attempts) == 1
    assert attempts[0]["success"] is True

    sess = db_session.get(AutomationSession, sid)
    assert sess.current_round_number == 1


def test_request_revision_adds_round_and_preserves_history(client, tmp_path: Path, monkeypatch, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-REV")
    sid = uuid.UUID(data["id"])
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    rv = client.post(
        f"/automation/sessions/{sid}/request-revision",
        json={
            "actor_id": "qa.lead",
            "instruction_text": "locator: use data-testid",
            "target_scope": "tests/smoke.spec.ts",
        },
    )
    assert rv.status_code == 200, rv.text

    rounds = client.get(f"/automation/sessions/{sid}/rounds").json()["items"]
    assert len(rounds) == 2
    assert rounds[0]["round_number"] == 1
    assert rounds[1]["round_number"] == 2
    assert rounds[1]["trigger_type"] == "review_revision"

    sess = db_session.get(AutomationSession, sid)
    assert sess.current_round_number == 2

    reviews = client.get(f"/automation/sessions/{sid}/review-requests").json()["items"]
    assert len(reviews) >= 1
    assert any(r["action_type"] == "request_revision" for r in reviews)


def test_only_one_current_plan_and_patch(client, tmp_path: Path, monkeypatch, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-CUR")
    sid = uuid.UUID(data["id"])
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    assert (
        client.post(
            f"/automation/sessions/{sid}/request-revision",
            json={"actor_id": "a", "instruction_text": "page object: tighten locator"},
        ).status_code
        == 200
    )

    n_cur_plans = db_session.scalar(
        select(func.count()).select_from(AutomationPlanVersion).where(
            AutomationPlanVersion.automation_session_id == sid,
            AutomationPlanVersion.is_current.is_(True),
        )
    )
    n_cur_patches = db_session.scalar(
        select(func.count()).select_from(AutomationPatchVersion).where(
            AutomationPatchVersion.automation_session_id == sid,
            AutomationPatchVersion.is_current.is_(True),
        )
    )
    assert int(n_cur_plans or 0) == 1
    assert int(n_cur_patches or 0) == 1


def test_manual_edit_ack_adds_round_and_execution(client, tmp_path: Path, monkeypatch, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-MAN")
    sid = uuid.UUID(data["id"])
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    ack = client.post(
        f"/automation/sessions/{sid}/manual-edit-ack",
        json={"actor_id": "qa.lead", "note": "Adjusted selectors manually."},
    )
    assert ack.status_code == 200, ack.text

    rounds = db_session.scalars(
        select(AutomationRevisionRound)
        .where(AutomationRevisionRound.automation_session_id == sid)
        .order_by(AutomationRevisionRound.round_number)
    ).all()
    assert len(rounds) == 2
    assert rounds[1].trigger_type == "manual_edit_rerun"

    attempts = client.get(f"/automation/sessions/{sid}/execution-attempts").json()["items"]
    assert len(attempts) >= 2


def test_approve_session_updates_job_and_status(client, tmp_path: Path, monkeypatch, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-APR")
    sid = uuid.UUID(data["id"])
    jid = uuid.UUID(data["automation_job_id"])
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    ap = client.post(
        f"/automation/sessions/{sid}/approve",
        json={"actor_id": "qa.lead"},
    )
    assert ap.status_code == 200, ap.text
    assert ap.json()["job_status"] == AutomationJobStatus.APPROVED_FOR_PR.value

    job = db_session.get(AutomationJob, jid)
    assert job.status == AutomationJobStatus.APPROVED_FOR_PR.value

    summ = client.get(f"/automation/sessions/{sid}").json()
    assert summ["status"] == AutomationSessionStatus.APPROVED_FOR_PR.value

    audits = db_session.execute(select(AuditLog).where(AuditLog.event_type == AuditEventType.AUTOMATION_SESSION_APPROVED.value)).scalars().all()
    assert len(audits) >= 1


def test_read_history_endpoints(client, tmp_path: Path, monkeypatch):
    data = _create_session(client, tmp_path, case_id="SESS-HIST")
    sid = data["id"]
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    for path in ("rounds", "plan-versions", "patch-versions", "execution-attempts", "review-requests"):
        r = client.get(f"/automation/sessions/{sid}/{path}")
        assert r.status_code == 200
        assert "items" in r.json()


def test_legacy_job_create_still_works(client):
    r = client.post(
        "/automation/jobs",
        json={"approved_case_id": "LEGACY-JOB", "requested_by": "u1"},
    )
    assert r.status_code == 201
    assert r.json()["approved_case_id"] == "LEGACY-JOB"


def test_unsupported_engine_on_create(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "BAD-ENG",
            "created_by": "u",
            "coding_engine": "claude_agent_sdk",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unsupported_coding_engine"


def test_approve_after_revision_still_works(client, tmp_path: Path, monkeypatch, db_session):
    data = _create_session(client, tmp_path, case_id="SESS-APR2")
    sid = uuid.UUID(data["id"])
    jid = uuid.UUID(data["automation_job_id"])
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    assert (
        client.post(
            f"/automation/sessions/{sid}/request-revision",
            json={"actor_id": "a", "instruction_text": "assertion: add expect visible"},
        ).status_code
        == 200
    )
    assert (
        client.post(f"/automation/sessions/{sid}/approve", json={"actor_id": "qa.lead"}).status_code == 200
    )
    job = db_session.get(AutomationJob, jid)
    assert job.status == AutomationJobStatus.APPROVED_FOR_PR.value


def test_session_start_idempotent_conflict(client, tmp_path: Path, monkeypatch):
    data = _create_session(client, tmp_path, case_id="SESS-DUP")
    sid = data["id"]
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    st2 = client.post(f"/automation/sessions/{sid}/start", json={})
    assert st2.status_code == 409


def test_create_session_rejects_invalid_repository_connection(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    bad = str(uuid.uuid4())
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "SESS-BAD-RC",
            "created_by": "u",
            "coding_engine": "stub",
            "repo_path": str(tmp_path.resolve()),
            "repository_connection_id": bad,
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "repository_connection_invalid"


def test_session_start_after_materialized_workspace_stub(client, tmp_path: Path, monkeypatch, db_session):
    import app.services.automation_session_service as ss
    from app.core.config import Settings
    from app.services.repo_bootstrap_service import bootstrap_node_workspace as real_bootstrap

    conn = create_repository_connection(
        db_session,
        provider="github",
        display_name="Demo",
        owner_or_org="acme",
        repo_name="widget",
        created_by="u",
    )
    db_session.commit()

    ws = tmp_path / "mat"
    ws.mkdir(parents=True, exist_ok=True)
    _playwright_fixture_repo(ws)

    npm_calls: list[list[str]] = []

    def fake_bootstrap(workspace, *, workspace_profile, settings=None, subprocess_runner=None):
        def fake_run(argv, *, cwd, timeout_seconds, env=None):
            npm_calls.append(list(argv))
            return _fake_npm_run_populates_hosted_layout(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env)

        return real_bootstrap(
            workspace,
            workspace_profile=workspace_profile,
            settings=settings or Settings(qswarm_bootstrap_timeout_seconds=120),
            subprocess_runner=fake_run,
        )

    def fake_prepare(db, *, session, job, repository_connection_id=None, settings=None):
        rp = str(ws.resolve())
        job.repo_path = rp
        session.repo_path = rp
        db.flush()
        return WorkspacePreparationResult(
            mode="cloned_workspace",
            workspace_path=rp,
            clone_url_used="https://github.com/acme/widget.git",
            provider="github",
            target_branch="main",
            source_reference=None,
        )

    monkeypatch.setattr(ss, "prepare_automation_session_workspace", fake_prepare)
    monkeypatch.setattr("app.services.framework_runtime_service.bootstrap_node_workspace", fake_bootstrap)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )

    cr = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "SESS-MAT",
            "created_by": "runner",
            "coding_engine": "stub",
            "repository_connection_id": str(conn.id),
            "case_title": "T",
            "steps": ["open"],
        },
    )
    assert cr.status_code == 201, cr.text
    assert cr.json().get("repository_connection_id") == str(conn.id)
    sid = uuid.UUID(cr.json()["id"])
    jid = uuid.UUID(cr.json()["automation_job_id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    job = db_session.get(AutomationJob, jid)
    assert job.repo_path == str(ws.resolve())
    assert any(c[:2] == ["npm", "ci"] for c in npm_calls), "hosted clone must run npm ci before execution"

    boot_fin = db_session.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_id == str(jid),
            AuditLog.event_type == AuditEventType.AUTOMATION_REPO_BOOTSTRAP.value,
        )
        .order_by(AuditLog.created_at)
    ).all()
    assert boot_fin, "expected bootstrap completion audit"
    rv = boot_fin[-1].event_payload_json.get("runtime_validation") or {}
    assert rv.get("success") is True
    checks = " ".join(rv.get("checks_run") or [])
    assert str(ws.resolve()) in checks.replace("\\", "/")
    prof = boot_fin[-1].event_payload_json.get("framework_runtime_profile") or {}
    assert prof.get("framework_name") == "playwright"


def test_session_start_hosted_validation_failure_blocks_execution_and_no_attempts(
    client, tmp_path: Path, monkeypatch, db_session
):
    import app.services.automation_session_service as ss
    from app.core.config import Settings
    from app.services.repo_bootstrap_service import bootstrap_node_workspace as real_bootstrap

    conn = create_repository_connection(
        db_session,
        provider="github",
        display_name="Demo",
        owner_or_org="acme",
        repo_name="widget",
        created_by="u",
    )
    db_session.commit()

    ws = tmp_path / "mat_bad"
    ws.mkdir(parents=True, exist_ok=True)
    _playwright_fixture_repo(ws)

    exec_calls: list[uuid.UUID] = []

    def track_execute(db, job_id, *, actor_id=None):
        exec_calls.append(job_id)

    def fake_bootstrap(workspace, *, workspace_profile, settings=None, subprocess_runner=None):
        def fake_run(argv, *, cwd, timeout_seconds, env=None):
            if argv == ["npm", "--version"]:
                return {"exit_code": 0, "stdout": "10", "stderr": "", "duration_ms": 1, "timed_out": False}
            return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

        return real_bootstrap(
            workspace,
            workspace_profile=workspace_profile,
            settings=settings or Settings(qswarm_bootstrap_timeout_seconds=120),
            subprocess_runner=fake_run,
        )

    def fake_prepare(db, *, session, job, repository_connection_id=None, settings=None):
        rp = str(ws.resolve())
        job.repo_path = rp
        session.repo_path = rp
        db.flush()
        return WorkspacePreparationResult(
            mode="cloned_workspace",
            workspace_path=rp,
            clone_url_used="https://github.com/acme/widget.git",
            provider="github",
            target_branch="main",
            source_reference=None,
        )

    monkeypatch.setattr(ss, "prepare_automation_session_workspace", fake_prepare)
    monkeypatch.setattr("app.services.framework_runtime_service.bootstrap_node_workspace", fake_bootstrap)
    monkeypatch.setattr("app.services.automation_job_service.execute_automation_job", track_execute)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )

    cr = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "SESS-MAT-VAL",
            "created_by": "runner",
            "coding_engine": "stub",
            "repository_connection_id": str(conn.id),
            "case_title": "T",
            "steps": ["open"],
        },
    )
    assert cr.status_code == 201, cr.text
    sid = uuid.UUID(cr.json()["id"])
    jid = uuid.UUID(cr.json()["automation_job_id"])
    st = client.post(f"/automation/sessions/{sid}/start", json={})
    assert st.status_code == 400, st.text
    assert st.json()["detail"]["code"] == "runtime_validation_failed"
    assert exec_calls == []

    db_session.expire_all()
    sess = db_session.get(AutomationSession, sid)
    job_row = db_session.get(AutomationJob, jid)
    assert sess is not None and job_row is not None
    assert sess.status == AutomationSessionStatus.FAILED.value
    assert job_row.status == AutomationJobStatus.FAILED.value
    assert sess.current_round_number == 0

    n_rounds = db_session.scalar(
        select(func.count()).select_from(AutomationRevisionRound).where(
            AutomationRevisionRound.automation_session_id == sid
        )
    )
    assert int(n_rounds or 0) == 0

    pre_fail = db_session.scalars(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.AUTOMATION_SESSION_START_PRE_ROUND_FAILED.value,
            AuditLog.entity_id == str(sid),
        )
    ).all()
    assert len(pre_fail) >= 1
    assert pre_fail[0].event_payload_json.get("stage") == "runtime_validation"

    n_attempts = db_session.scalar(
        select(func.count())
        .select_from(AutomationExecutionAttempt)
        .where(AutomationExecutionAttempt.automation_session_id == sid)
    )
    assert int(n_attempts or 0) == 0


def test_session_start_invokes_bootstrap(client, tmp_path: Path, monkeypatch, db_session):
    import app.services.automation_session_service as mod

    profiles: list[tuple[str, str | None]] = []
    orig = mod._run_repo_bootstrap_for_session

    def wrap(db, *, session, job, actor_id, workspace_profile, prep_mode=None):
        profiles.append((workspace_profile, prep_mode))
        return orig(
            db,
            session=session,
            job=job,
            actor_id=actor_id,
            workspace_profile=workspace_profile,
            prep_mode=prep_mode,
        )

    monkeypatch.setattr(mod, "_run_repo_bootstrap_for_session", wrap)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    data = _create_session(client, tmp_path, case_id="SESS-BOOT-CALL")
    sid = uuid.UUID(data["id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200
    assert profiles == [("local_existing", "existing_path")]


def test_session_start_bootstrap_failure_returns_400_and_skips_execution(
    client, tmp_path: Path, monkeypatch, db_session
):
    import app.services.automation_session_service as mod
    from app.services.repo_bootstrap_service import RepoBootstrapError

    exec_calls: list[uuid.UUID] = []

    def track_execute(db, job_id, *, actor_id=None):
        exec_calls.append(job_id)

    def fail_bootstrap(*args, **kwargs):
        raise RepoBootstrapError("simulated npm failure", code="repo_bootstrap_failed")

    monkeypatch.setattr(mod, "bootstrap_node_workspace", fail_bootstrap)
    monkeypatch.setattr("app.services.automation_job_service.execute_automation_job", track_execute)

    data = _create_session(client, tmp_path, case_id="SESS-BOOT-FAIL")
    sid = uuid.UUID(data["id"])
    r = client.post(f"/automation/sessions/{sid}/start", json={})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "repo_bootstrap_failed"
    assert exec_calls == []

    db_session.expire_all()
    sess = db_session.get(AutomationSession, sid)
    job_row = db_session.get(AutomationJob, uuid.UUID(data["automation_job_id"]))
    assert sess.status == AutomationSessionStatus.FAILED.value
    assert job_row.status == AutomationJobStatus.FAILED.value

    pre = db_session.scalars(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.AUTOMATION_SESSION_START_PRE_ROUND_FAILED.value,
            AuditLog.entity_id == str(sid),
        )
    ).all()
    assert pre and pre[0].event_payload_json.get("stage") == "bootstrap"

    n_rounds = db_session.scalar(
        select(func.count()).select_from(AutomationRevisionRound).where(
            AutomationRevisionRound.automation_session_id == sid
        )
    )
    assert int(n_rounds or 0) == 0


def test_session_start_workspace_prep_failure_marks_session_and_job_failed(
    client, tmp_path: Path, monkeypatch, db_session
):
    import app.services.automation_session_service as ss
    from app.services.repo_workspace_service import RepoCloneError

    def fail_prep(*args, **kwargs):
        raise RepoCloneError("simulated clone failure", code="repo_clone_failed")

    monkeypatch.setattr(ss, "prepare_automation_session_workspace", fail_prep)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    data = _create_session(client, tmp_path, case_id="SESS-WSP-FAIL")
    sid = uuid.UUID(data["id"])
    jid = uuid.UUID(data["automation_job_id"])
    r = client.post(f"/automation/sessions/{sid}/start", json={})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "repo_clone_failed"

    db_session.expire_all()
    sess = db_session.get(AutomationSession, sid)
    job_row = db_session.get(AutomationJob, jid)
    assert sess.status == AutomationSessionStatus.FAILED.value
    assert job_row.status == AutomationJobStatus.FAILED.value

    pre = db_session.scalars(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.AUTOMATION_SESSION_START_PRE_ROUND_FAILED.value,
            AuditLog.entity_id == str(sid),
        )
    ).all()
    assert pre and pre[0].event_payload_json.get("stage") == "workspace_prep"


def test_session_start_writes_bootstrap_started_and_success_audit(
    client, tmp_path: Path, monkeypatch, db_session
):
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    data = _create_session(client, tmp_path, case_id="SESS-BOOT-AUD")
    sid = uuid.UUID(data["id"])
    jid = uuid.UUID(data["automation_job_id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    logs = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_id == str(jid), AuditLog.step_name == "repo_bootstrap")
        .order_by(AuditLog.created_at)
    ).all()
    et = [x.event_type for x in logs]
    assert AuditEventType.AUTOMATION_REPO_BOOTSTRAP_STARTED.value in et
    fin = [x for x in logs if x.event_type == AuditEventType.AUTOMATION_REPO_BOOTSTRAP.value]
    assert fin, "expected completion audit for repo bootstrap"
    assert fin[-1].event_payload_json.get("success") is True


def test_session_start_bootstrap_failure_audit_records_failure_payload(
    client, tmp_path: Path, monkeypatch, db_session
):
    import app.services.automation_session_service as mod
    from app.services.repo_bootstrap_service import RepoBootstrapError

    def fail_bootstrap(*args, **kwargs):
        raise RepoBootstrapError("simulated", code="repo_bootstrap_failed")

    monkeypatch.setattr(mod, "bootstrap_node_workspace", fail_bootstrap)

    data = _create_session(client, tmp_path, case_id="SESS-BOOT-AUD-FAIL")
    sid = uuid.UUID(data["id"])
    jid = uuid.UUID(data["automation_job_id"])
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 400

    logs = db_session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_id == str(jid), AuditLog.step_name == "repo_bootstrap")
        .order_by(AuditLog.created_at)
    ).all()
    et = [x.event_type for x in logs]
    assert AuditEventType.AUTOMATION_REPO_BOOTSTRAP_STARTED.value in et
    fin = [x for x in logs if x.event_type == AuditEventType.AUTOMATION_REPO_BOOTSTRAP.value]
    assert fin[-1].event_payload_json.get("success") is False
    assert fin[-1].event_payload_json.get("code") == "repo_bootstrap_failed"
