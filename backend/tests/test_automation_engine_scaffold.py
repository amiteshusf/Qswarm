"""Sprint 2 Milestone 1: coding-agent adapter scaffold tests."""

import uuid
from pathlib import Path

import pytest

from app.automation_engine.coding_engine_names import CodingEngineName
from app.automation_engine.engine_errors import EngineConfigurationError, UnsupportedEngineError
from app.automation_engine.engine_models import EngineRequest, EngineTaskType
from app.automation_engine.registry import (
    list_adapter_capabilities,
    list_known_engines,
    resolve_coding_agent_adapter,
    supported_coding_engines,
)
from app.automation_engine.stub_adapter import StubCodingAgentAdapter
from app.core.config import get_settings
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.services.automation_engine_payload_builder import AutomationEnginePayloadBuilder
from test_automation_jobs import _playwright_fixture_repo


def test_coding_engine_name_parse():
    assert CodingEngineName.parse("STUB") == CodingEngineName.STUB
    assert CodingEngineName.parse("claude_code") == CodingEngineName.CLAUDE_CODE
    with pytest.raises(ValueError, match="unsupported_coding_engine"):
        CodingEngineName.parse("unknown_xyz")


def test_supported_engine_names():
    assert supported_coding_engines() == frozenset({"stub", "claude_code", "copilot_agent"})
    assert set(list_known_engines()) == {"claude_code", "copilot_agent", "stub"}


def test_registry_resolves_all_known():
    assert isinstance(resolve_coding_agent_adapter("stub"), StubCodingAgentAdapter)
    assert resolve_coding_agent_adapter("claude_code").engine_name == "claude_code"
    assert resolve_coding_agent_adapter("copilot_agent").engine_name == "copilot_agent"


def test_registry_unknown_raises():
    with pytest.raises(UnsupportedEngineError):
        resolve_coding_agent_adapter("not_an_engine")


def test_capabilities_stub_configured():
    caps = {c.engine_name: c for c in list_adapter_capabilities()}
    assert caps["stub"].configured is True
    assert caps["stub"].supports_plan is True


def test_capabilities_placeholders_unconfigured_by_default(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "false")
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        caps = {c.engine_name: c for c in list_adapter_capabilities()}
        assert caps["claude_code"].configured is False
        assert caps["copilot_agent"].configured is False
    finally:
        get_settings.cache_clear()


def test_capabilities_placeholders_configured_when_env(monkeypatch):
    import sys

    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "true")
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_COMMAND", sys.executable)
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", "-c pass")
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_ENABLED", "true")
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_COMMAND", sys.executable)
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_EXTRA_ARGS", "-c pass")
    get_settings.cache_clear()
    try:
        caps = {c.engine_name: c for c in list_adapter_capabilities()}
        assert caps["claude_code"].configured is True
        assert caps["copilot_agent"].configured is True
    finally:
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_ENABLED", raising=False)
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_COMMAND", raising=False)
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", raising=False)
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_ENABLED", raising=False)
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_COMMAND", raising=False)
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_EXTRA_ARGS", raising=False)
        get_settings.cache_clear()


def test_placeholder_run_initial_raises_when_disabled(monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        ad = resolve_coding_agent_adapter("claude_code")
        req = EngineRequest(
            session_id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            round_id=str(uuid.uuid4()),
            engine_name="claude_code",
            task_type=EngineTaskType.INITIAL_GENERATION,
        )
        with pytest.raises(EngineConfigurationError, match="disabled"):
            ad.run_initial_request(req, context=None)  # type: ignore[arg-type]
    finally:
        get_settings.cache_clear()


def test_claude_run_initial_with_none_context_errors_when_enabled(monkeypatch):
    import sys

    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "true")
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_COMMAND", sys.executable)
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", "-c pass")
    get_settings.cache_clear()
    try:
        ad = resolve_coding_agent_adapter("claude_code")
        req = EngineRequest(
            session_id=str(uuid.uuid4()),
            job_id=str(uuid.uuid4()),
            round_id=str(uuid.uuid4()),
            engine_name="claude_code",
            task_type=EngineTaskType.INITIAL_GENERATION,
        )
        with pytest.raises((AttributeError, TypeError)):
            ad.run_initial_request(req, context=None)  # type: ignore[arg-type]
    finally:
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_ENABLED", raising=False)
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_COMMAND", raising=False)
        monkeypatch.delenv("QSWARM_CLAUDE_CODE_EXTRA_ARGS", raising=False)
        get_settings.cache_clear()


def test_payload_builder_shapes(db_session, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    job = AutomationJob(
        approved_case_id="PAYLOAD-1",
        requested_by="u",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        status="pending",
    )
    db_session.add(job)
    db_session.flush()
    sess = AutomationSession(
        source_system="jira",
        source_reference="ABC-1",
        automation_job_id=job.id,
        repo_owner="o",
        repo_name="r",
        repo_path=str(tmp_path.resolve()),
        base_branch="main",
        coding_engine="stub",
        status="pending",
        current_round_number=0,
        approved_case_id="PAYLOAD-1",
        created_by="u",
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

    b = AutomationEnginePayloadBuilder()
    ini = b.build_initial_request(sess, job, rnd, actor_id="actor")
    assert ini.session_id == str(sess.id)
    assert ini.job_id == str(job.id)
    assert ini.round_id == str(rnd.id)
    assert ini.engine_name == "stub"
    assert ini.source_type == "jira"
    assert ini.source_reference == "ABC-1"
    assert ini.repo_url == "https://github.com/o/r"

    rev = b.build_revision_request(
        sess, job, rnd, actor_id="a", instruction_text="fix locator", target_scope="tests/x.ts"
    )
    assert rev.task_type == EngineTaskType.REVISION
    assert rev.revision_instruction == "fix locator"
    assert rev.target_scope == "tests/x.ts"

    man = b.build_manual_rerun_request(sess, job, rnd, actor_id="a", note="edited locally")
    assert man.task_type == EngineTaskType.MANUAL_RERUN
    assert man.revision_instruction == "edited locally"


def test_engine_capabilities_endpoint(client):
    r = client.get("/automation/sessions/engine-capabilities")
    assert r.status_code == 200
    names = {x["engine_name"] for x in r.json()["items"]}
    assert names == {"claude_code", "copilot_agent", "stub"}


def test_session_create_accepts_claude_code(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/sessions",
        json={
            "approved_case_id": "CLAUDE-CREATE",
            "created_by": "u",
            "coding_engine": "claude_code",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    assert r.status_code == 201
    assert r.json()["coding_engine"] == "claude_code"


def test_session_start_claude_fails_configuration(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QSWARM_CLAUDE_CODE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        _playwright_fixture_repo(tmp_path)
        r = client.post(
            "/automation/sessions",
            json={
                "approved_case_id": "CLAUDE-START",
                "created_by": "u",
                "coding_engine": "claude_code",
                "repo_path": str(tmp_path.resolve()),
            },
        )
        sid = r.json()["id"]
        st = client.post(f"/automation/sessions/{sid}/start", json={})
        assert st.status_code == 400
        assert st.json()["detail"]["code"] == "engine_configuration"
    finally:
        get_settings.cache_clear()


def test_session_start_copilot_fails_configuration(client, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        _playwright_fixture_repo(tmp_path)
        r = client.post(
            "/automation/sessions",
            json={
                "approved_case_id": "COPILOT-START",
                "created_by": "u",
                "coding_engine": "copilot_agent",
                "repo_path": str(tmp_path.resolve()),
            },
        )
        sid = r.json()["id"]
        st = client.post(f"/automation/sessions/{sid}/start", json={})
        assert st.status_code == 400
        assert st.json()["detail"]["code"] == "engine_configuration"
    finally:
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_ENABLED", raising=False)
        get_settings.cache_clear()
