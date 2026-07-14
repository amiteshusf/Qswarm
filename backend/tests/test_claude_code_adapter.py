"""Claude Code adapter (Sprint 2 Phase 2) — subprocess path + config; no real Claude binary required."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

from app.automation_engine.claude_code_adapter import ClaudeCodeAdapter
from app.automation_engine.claude_workspace_patch import plan_paths_in_order
from app.automation_engine.engine_errors import (
    EngineAdapterError,
    EngineConfigurationError,
    EngineRepoAccessError,
    EngineTimeoutError,
)
from app.automation_engine.engine_models import EngineRequest, EngineTaskType
from app.automation_engine.registry import list_adapter_capabilities, resolve_coding_agent_adapter
from app.automation_engine.types import CodeSessionContext
from app.core.config import get_settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.providers.coding.stub_provider import _playwright_spec_stub
from app.services.automation_engine_payload_builder import AutomationEnginePayloadBuilder
from test_automation_jobs import _playwright_fixture_repo, _stub_execution_run_factory


@pytest.fixture
def claude_env(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "true")
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_COMMAND", sys.executable)
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", "-c pass")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("QSWARM_CLAUDE_CODE_ENABLED", raising=False)
    monkeypatch.delenv("QSWARM_CLAUDE_CODE_COMMAND", raising=False)
    monkeypatch.delenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", raising=False)
    get_settings.cache_clear()


def test_claude_validate_config_success(claude_env):
    ad = ClaudeCodeAdapter()
    assert ad.validate_config() is True


def test_claude_validate_config_disabled_returns_false(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert ClaudeCodeAdapter().validate_config() is False
    finally:
        get_settings.cache_clear()


def test_claude_validate_config_raises_when_enabled_but_command_empty(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "true")
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_COMMAND", " ")
    get_settings.cache_clear()
    try:
        with pytest.raises(EngineConfigurationError, match="COMMAND"):
            ClaudeCodeAdapter().validate_config()
    finally:
        get_settings.cache_clear()


def test_claude_validate_config_raises_when_cli_missing(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "true")
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_COMMAND", "/nonexistent/claude_cli_zz")
    get_settings.cache_clear()
    try:
        with pytest.raises(EngineConfigurationError, match="not found"):
            ClaudeCodeAdapter().validate_config()
    finally:
        get_settings.cache_clear()


def test_claude_repo_path_missing_raises(monkeypatch, db_session, tmp_path: Path, claude_env):
    _playwright_fixture_repo(tmp_path)
    job = AutomationJob(
        approved_case_id="C1",
        requested_by="u",
        repo_path="",
        base_branch="main",
        status="pending",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        coding_engine="claude_code",
        status="pending",
        current_round_number=0,
        approved_case_id="C1",
        created_by="u",
        repo_path="",
    )
    db_session.add(sess)
    db_session.flush()
    rnd = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="u",
        trigger_type="initial",
        status="in_progress",
    )
    db_session.add(rnd)
    db_session.flush()
    ctx = CodeSessionContext(db=db_session, session=sess, job=job, actor_id="u", revision_round=rnd)
    b = AutomationEnginePayloadBuilder()
    req = b.build_initial_request(sess, job, rnd, actor_id="u")
    ad = ClaudeCodeAdapter()
    with pytest.raises(EngineRepoAccessError, match="repo_path"):
        ad.run_initial_request(req, context=ctx)


def test_claude_subprocess_timeout_raises(monkeypatch, db_session, tmp_path: Path, claude_env):
    _playwright_fixture_repo(tmp_path)
    job = AutomationJob(
        approved_case_id="C2",
        requested_by="u",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="pending",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        coding_engine="claude_code",
        status="pending",
        current_round_number=0,
        approved_case_id="C2",
        created_by="u",
        repo_path=str(tmp_path.resolve()),
    )
    db_session.add(sess)
    db_session.flush()
    rnd = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="u",
        trigger_type="initial",
        status="in_progress",
    )
    db_session.add(rnd)
    db_session.flush()
    ctx = CodeSessionContext(db=db_session, session=sess, job=job, actor_id="u", revision_round=rnd)
    req = AutomationEnginePayloadBuilder().build_initial_request(sess, job, rnd, actor_id="u")

    def _timed_out(argv, **kwargs):
        return {
            "exit_code": None,
            "stdout": "",
            "stderr": "timeout",
            "duration_ms": 1,
            "timed_out": True,
        }

    monkeypatch.setattr("app.automation_engine.claude_code_adapter.run_subprocess_argv", _timed_out)
    ad = ClaudeCodeAdapter()
    with pytest.raises(EngineTimeoutError):
        ad.run_initial_request(req, context=ctx)


def test_registry_claude_configured_reflects_env(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "true")
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_COMMAND", sys.executable)
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", "-c pass")
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        caps = {c.engine_name: c for c in list_adapter_capabilities()}
        assert caps["claude_code"].configured is True
        assert caps["copilot_agent"].configured is False
    finally:
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_ENABLED", raising=False)
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_COMMAND", raising=False)
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", raising=False)
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_ENABLED", raising=False)
        get_settings.cache_clear()


def test_claude_nonzero_exit_raises_adapter_error(monkeypatch, db_session, tmp_path: Path, claude_env):
    _playwright_fixture_repo(tmp_path)
    job = AutomationJob(
        approved_case_id="C3",
        requested_by="u",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="pending",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        automation_job_id=job.id,
        coding_engine="claude_code",
        status="pending",
        current_round_number=0,
        approved_case_id="C3",
        created_by="u",
        repo_path=str(tmp_path.resolve()),
    )
    db_session.add(sess)
    db_session.flush()
    rnd = AutomationRevisionRound(
        automation_session_id=sess.id,
        round_number=1,
        started_by="u",
        trigger_type="initial",
        status="in_progress",
    )
    db_session.add(rnd)
    db_session.flush()
    ctx = CodeSessionContext(db=db_session, session=sess, job=job, actor_id="u", revision_round=rnd)
    req = AutomationEnginePayloadBuilder().build_initial_request(sess, job, rnd, actor_id="u")

    def _bad(argv, **kwargs):
        return {
            "exit_code": 2,
            "stdout": "",
            "stderr": "nope",
            "duration_ms": 1,
            "timed_out": False,
        }

    monkeypatch.setattr("app.automation_engine.claude_code_adapter.run_subprocess_argv", _bad)
    with pytest.raises(EngineAdapterError, match="exited"):
        ClaudeCodeAdapter().run_initial_request(req, context=ctx)


def _write_plan_files_from_job(job: AutomationJob, root: Path) -> None:
    plan = job.change_plan_json if isinstance(job.change_plan_json, dict) else {}
    spec = job.case_spec_json if isinstance(job.case_spec_json, dict) else {"title": "T"}
    body = _playwright_spec_stub(spec)
    for key in ("files_to_modify", "files_to_create"):
        for rel in plan.get(key) or []:
            if not isinstance(rel, str):
                continue
            p = root / rel.strip().replace("\\", "/")
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel.endswith(".ts"):
                p.write_text(body)
            else:
                p.write_text(f"// QSwarm stub touch {rel}\nexport const ok = true;\n")


def test_session_start_claude_invokes_subprocess_and_finishes(
    client, tmp_path: Path, monkeypatch, db_session, claude_env
):
    _playwright_fixture_repo(tmp_path)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )

    calls: list[dict] = []

    def fake_run(argv: list, **kwargs):
        calls.append({"argv": list(argv), "cwd": kwargs.get("cwd")})
        jid = kwargs.get("_job_id")
        if jid:
            j = db_session.get(AutomationJob, jid)
            if j and isinstance(j.change_plan_json, dict):
                _write_plan_files_from_job(j, Path(str(kwargs["cwd"])))
        return {"exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 3, "timed_out": False}

    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "CLAUDE-FLOW",
            "created_by": "runner",
            "coding_engine": "claude_code",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke",
            "steps": ["open app"],
        },
    )
    assert r.status_code == 201, r.text
    sid = uuid.UUID(r.json()["id"])
    jid = uuid.UUID(r.json()["automation_job_id"])

    def run_wrapped(argv, cwd=None, timeout_seconds=None, env=None):
        return fake_run(argv, cwd=cwd, timeout_seconds=timeout_seconds, env=env, _job_id=jid)

    monkeypatch.setattr("app.automation_engine.claude_code_adapter.run_subprocess_argv", run_wrapped)
    st = client.post(f"/automation/sessions/{sid}/start", json={})
    assert st.status_code == 200, st.text
    assert len(calls) == 1
    assert Path(calls[0]["cwd"]).resolve() == tmp_path.resolve()
    assert "-p" in calls[0]["argv"] or "--print" in calls[0]["argv"]
    assert st.json()["job_status"] == "awaiting_automation_review"


def test_session_revision_claude_invokes_subprocess(
    client, tmp_path: Path, monkeypatch, db_session, claude_env
):
    from test_automation_sessions import _patch_playwright_run_for_job_and_review

    _playwright_fixture_repo(tmp_path)
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())

    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "CLAUDE-REV",
            "created_by": "runner",
            "coding_engine": "claude_code",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke",
            "steps": ["open app"],
        },
    )
    sid = uuid.UUID(r.json()["id"])
    jid = uuid.UUID(r.json()["automation_job_id"])

    calls: list[list[str]] = []
    call_count = {"n": 0}

    def run_wrapped(argv, cwd=None, timeout_seconds=None, env=None):
        call_count["n"] += 1
        calls.append(list(argv))
        j = db_session.get(AutomationJob, jid)
        root = Path(str(cwd))
        if j and isinstance(j.change_plan_json, dict):
            _write_plan_files_from_job(j, root)
            if call_count["n"] > 1:
                for rel in plan_paths_in_order(j):
                    p = root / rel
                    if p.is_file():
                        p.write_text(p.read_text(encoding="utf-8") + "\n// revision\n", encoding="utf-8")
        return {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1, "timed_out": False}

    monkeypatch.setattr("app.automation_engine.claude_code_adapter.run_subprocess_argv", run_wrapped)
    assert client.post(f"/automation/sessions/{sid}/start", json={}).status_code == 200

    assert (
        client.post(
            f"/automation/sessions/{sid}/request-revision",
            json={"actor_id": "qa", "instruction_text": "tighten locator"},
        ).status_code
        == 200
    )
    assert len(calls) >= 2
    assert any("-p" in a or "--print" in a for a in calls)


def test_resolve_claude_uses_real_adapter_class(claude_env):
    ad = resolve_coding_agent_adapter("claude_code")
    assert ad.__class__.__name__ == "ClaudeCodeAdapter"
