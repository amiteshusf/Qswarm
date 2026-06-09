"""Automation job API tests."""

import json
import uuid

from pathlib import Path

from sqlalchemy import select

from app.core.constants import AuditEventType, AutomationJobReviewActionType, AutomationJobStatus
from app.db.models.audit_log import AuditLog
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_job_review_action import AutomationJobReviewAction


def _playwright_fixture_repo(root: Path) -> None:
    (root / "playwright.config.ts").write_text("export default {};\n")
    (root / "package.json").write_text('{"devDependencies":{"@playwright/test":"^1.0.0"}}')
    # Minimal coherent lock so `npm ci` selection matches real repos (empty `{}` is not usable for npm ci).
    lock = {"lockfileVersion": 3, "packages": {"": {"name": "fixture", "version": "1.0.0"}}}
    (root / "package-lock.json").write_text(json.dumps(lock))
    nm = root / "node_modules"
    nm.mkdir(exist_ok=True)
    (nm / ".qswarm_test_stub").write_text("1")
    d = root / "tests"
    d.mkdir()
    (d / "smoke.spec.ts").write_text("// test\n")


def _ensure_git_repo_for_session_pr(root: Path) -> None:
    """Minimal git work tree so session create-pr workspace checks pass before the pipeline runs."""
    import os
    import subprocess

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "config", "user.email", "fixture@test.local"],
        cwd=str(root),
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "fixture"],
        cwd=str(root),
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(root), check=True, capture_output=True, env=env)


def _playwright_auth_repo(root: Path) -> None:
    _playwright_fixture_repo(root)
    auth = root / "tests" / "auth"
    auth.mkdir(parents=True, exist_ok=True)
    (auth / "login.spec.ts").write_text("// login\n")
    (auth / "forgot-password.spec.ts").write_text("// forgot\n")
    (root / "pages").mkdir()
    (root / "pages" / "LoginPage.ts").write_text("export class L {}\n")
    (root / "pages" / "ForgotPasswordPage.ts").write_text("export class F {}\n")
    (root / "utils").mkdir()
    (root / "utils" / "mailhog.ts").write_text("export const m = 1\n")
    fx = root / "tests" / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    (fx / "auth.fixture.ts").write_text("export const a = 1\n")


def test_create_automation_job_201(client):
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-001",
            "requested_by": "qa_lead",
            "repo_id": "acme/web",
            "base_branch": "develop",
        },
    )
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["approved_case_id"] == "CASE-001"
    assert b["status"] == AutomationJobStatus.PENDING.value
    assert b["workflow_run_id"] is None
    assert b["repo_id"] == "acme/web"
    assert b["base_branch"] == "develop"
    assert b.get("framework_summary_json") is None
    assert b.get("framework_type") is None
    assert b.get("case_spec_json") is None
    assert b.get("repo_context_json") is None


def test_get_automation_job_200(client):
    r = client.post(
        "/automation/jobs",
        json={"approved_case_id": "CASE-GET", "requested_by": "u1"},
    )
    jid = r.json()["id"]
    g = client.get(f"/automation/jobs/{jid}")
    assert g.status_code == 200
    assert g.json()["id"] == jid
    assert g.json()["approved_case_id"] == "CASE-GET"


def test_list_automation_jobs(client):
    client.post(
        "/automation/jobs",
        json={"approved_case_id": "CASE-LIST-A", "requested_by": "u"},
    )
    client.post(
        "/automation/jobs",
        json={"approved_case_id": "CASE-LIST-B", "requested_by": "u"},
    )
    lst = client.get("/automation/jobs")
    assert lst.status_code == 200
    items = lst.json()["items"]
    keys = {x["approved_case_id"] for x in items}
    assert "CASE-LIST-A" in keys
    assert "CASE-LIST-B" in keys


def test_start_job_requires_repo_path(client):
    r = client.post(
        "/automation/jobs",
        json={"approved_case_id": "CASE-NO-REPO", "requested_by": "runner"},
    )
    jid = r.json()["id"]
    s = client.post(f"/automation/jobs/{jid}/start")
    assert s.status_code == 400
    assert s.json()["detail"]["code"] == "repo_path_required"


def test_start_job_invalid_repo_path(client, tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-BAD-PATH",
            "requested_by": "runner",
            "repo_path": str(missing),
        },
    )
    jid = r.json()["id"]
    s = client.post(f"/automation/jobs/{jid}/start")
    assert s.status_code == 400
    assert s.json()["detail"]["code"] == "repo_path_not_found"


def test_start_job_repo_path_is_file(client, tmp_path: Path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x")
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-FILE",
            "requested_by": "runner",
            "repo_path": str(f),
        },
    )
    jid = r.json()["id"]
    s = client.post(f"/automation/jobs/{jid}/start")
    assert s.status_code == 400
    assert s.json()["detail"]["code"] == "repo_path_not_a_directory"


def test_start_playwright_job_planning_changes_and_context(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PW",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke login",
            "steps": ["open app", "log in"],
            "expected_results": ["dashboard visible"],
        },
    )
    jid = r.json()["id"]
    s = client.post(f"/automation/jobs/{jid}/start")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["status"] == AutomationJobStatus.PLANNING_CHANGES.value
    assert "planning" in body["message"].lower()

    g = client.get(f"/automation/jobs/{jid}")
    assert g.status_code == 200
    data = g.json()
    assert data["framework_type"] == "playwright"
    assert data["framework_summary_json"] is not None
    assert data["framework_summary_json"]["framework_type"] == "playwright"
    assert "npx playwright test" in data["framework_summary_json"].get("runner_command", "")
    assert data["case_spec_json"] is not None
    assert data["case_spec_json"]["title"] == "Smoke login"
    assert data["repo_context_json"] is not None
    assert data["repo_context_json"]["framework_type"] == "playwright"
    assert isinstance(data["repo_context_json"].get("similar_test_files"), list)


def test_start_playwright_auth_case_prioritizes_auth_paths(client, tmp_path: Path):
    _playwright_auth_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-AUTH",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Reset password with OTP",
            "steps": ["request reset", "enter OTP", "set password"],
            "expected_results": ["login with new password"],
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["status"] == AutomationJobStatus.PLANNING_CHANGES.value
    sim = data["repo_context_json"]["similar_test_files"]
    assert any("forgot-password" in x or "login" in x for x in sim)
    pages = data["repo_context_json"]["related_page_objects"]
    assert any("Login" in x or "Forgot" in x for x in pages)


def test_start_unknown_framework_job_failed_with_summary(client, tmp_path: Path):
    (tmp_path / "foo.txt").write_text("bar")
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-UNK",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    s = client.post(f"/automation/jobs/{jid}/start")
    assert s.status_code == 200
    assert s.json()["status"] == AutomationJobStatus.FAILED.value

    g = client.get(f"/automation/jobs/{jid}")
    data = g.json()
    assert data["status"] == AutomationJobStatus.FAILED.value
    assert data["blocked_reason"] == "Unsupported or unknown framework"
    assert data["framework_summary_json"]["framework_type"] == "unknown"
    assert data.get("case_spec_json") is None
    assert data.get("repo_context_json") is None


def test_start_job_twice_conflict(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-2X",
            "requested_by": "u",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    r2 = client.post(f"/automation/jobs/{jid}/start")
    assert r2.status_code == 409


def test_get_automation_job_404(client):
    fake = uuid.uuid4()
    r = client.get(f"/automation/jobs/{fake}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_create_job_invalid_workflow_run(client):
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "X",
            "requested_by": "u",
            "workflow_run_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 400


def test_repo_context_failure_marks_job_failed(client, tmp_path: Path, monkeypatch):
    from app.services import automation_job_service as ajs
    from app.services.repo_context_service import RepoContextError

    _playwright_fixture_repo(tmp_path)

    def _boom(*_a, **_k):
        raise RepoContextError("simulated scan failure")

    monkeypatch.setattr(ajs, "collect_repo_context", _boom)

    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-CTX-FAIL",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    s = client.post(f"/automation/jobs/{jid}/start")
    assert s.status_code == 200
    assert s.json()["status"] == AutomationJobStatus.FAILED.value
    data = client.get(f"/automation/jobs/{jid}").json()
    assert "simulated" in (data.get("blocked_reason") or "")
    assert data.get("case_spec_json") is not None
    assert data.get("repo_context_json") is None


def test_audit_on_create_and_start(client, tmp_path: Path, db_session):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-AUD",
            "requested_by": "auditor",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_JOB_CREATED.value in types

    client.post(f"/automation/jobs/{jid}/start")
    rows2 = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types2 = {x.event_type for x in rows2}
    assert AuditEventType.AUTOMATION_JOB_STARTED.value in types2
    assert AuditEventType.AUTOMATION_FRAMEWORK_SCAN_STARTED.value in types2
    assert AuditEventType.AUTOMATION_FRAMEWORK_SCAN_COMPLETED.value in types2
    assert AuditEventType.AUTOMATION_CASE_SPEC_BUILT.value in types2
    assert AuditEventType.AUTOMATION_REPO_CONTEXT_COLLECTED.value in types2


def test_plan_job_moves_to_generating_code(client, tmp_path: Path):
    _playwright_auth_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PLAN-OK",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Reset password with OTP",
            "steps": ["request reset", "enter OTP"],
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    p = client.post(f"/automation/jobs/{jid}/plan")
    assert p.status_code == 200, p.text
    body = p.json()
    assert body["status"] == AutomationJobStatus.GENERATING_CODE.value
    assert "success" in body["message"].lower()

    g = client.get(f"/automation/jobs/{jid}").json()
    assert g["status"] == AutomationJobStatus.GENERATING_CODE.value
    assert g["change_plan_json"] is not None
    assert g["change_plan_json"]["framework_type"] == "playwright"
    assert "forgot-password" in g["change_plan_json"]["target_test_file"]
    reuse = [x.lower() for x in g["change_plan_json"]["files_to_reuse"]]
    assert any("mailhog" in x for x in reuse)


def test_plan_job_wrong_state_returns_409(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PLAN-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    p2 = client.post(f"/automation/jobs/{jid}/plan")
    assert p2.status_code == 409


def test_plan_job_prerequisites_missing_returns_400(client, tmp_path: Path, db_session):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PLAN-PRE",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 409

    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    job = db_session.get(AutomationJob, jid)
    job.repo_context_json = None
    db_session.commit()

    p = client.post(f"/automation/jobs/{jid}/plan")
    assert p.status_code == 400
    assert p.json()["detail"]["code"] == "plan_prerequisites_missing"


def test_plan_job_invalid_plan_returns_422_and_marks_failed(client, tmp_path: Path, monkeypatch):
    from app.providers.coding.stub_provider import StubCodingProvider

    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PLAN-BAD",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200

    def _broken(self, payload):
        return {"framework_type": "playwright"}

    monkeypatch.setattr(StubCodingProvider, "create_change_plan", _broken)

    p = client.post(f"/automation/jobs/{jid}/plan")
    assert p.status_code == 422
    assert p.json()["detail"]["code"] == "invalid_change_plan"

    g = client.get(f"/automation/jobs/{jid}").json()
    assert g["status"] == AutomationJobStatus.FAILED.value
    assert g["change_plan_json"] is None
    assert g.get("blocked_reason")


def test_audit_change_planning_events(client, tmp_path: Path, db_session):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-PLAN-AUD",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    client.post(f"/automation/jobs/{jid}/start")
    client.post(f"/automation/jobs/{jid}/plan")

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_CHANGE_PLANNING_STARTED.value in types
    assert AuditEventType.AUTOMATION_CHANGE_PLAN_CREATED.value in types


def test_generate_job_applies_files_and_moves_to_executing(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-GEN-OK",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke flow",
            "steps": ["open app"],
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200

    g = client.post(f"/automation/jobs/{jid}/generate")
    assert g.status_code == 200, g.text
    assert g.json()["status"] == AutomationJobStatus.EXECUTING.value
    assert "applied" in g.json()["message"].lower() or "success" in g.json()["message"].lower()

    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["generated_patch_json"] is not None
    assert data["generated_patch_json"].get("provider") == "stub"
    assert data["generated_patch_json"].get("apply_result", {}).get("success") is True
    spec_path = tmp_path / "tests" / "smoke.spec.ts"
    text = spec_path.read_text(encoding="utf-8")
    assert "@playwright/test" in text or "QSwarm stub" in text


def test_generate_job_wrong_state_returns_409(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-GEN-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 409


def test_generate_job_prerequisites_missing_returns_400(client, tmp_path: Path, db_session):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-GEN-PRE",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    job = db_session.get(AutomationJob, jid)
    job.change_plan_json = None
    db_session.commit()
    gen = client.post(f"/automation/jobs/{jid}/generate")
    assert gen.status_code == 400
    assert gen.json()["detail"]["code"] == "generation_prerequisites_missing"


def test_generate_job_invalid_patch_returns_422(client, tmp_path: Path, monkeypatch):
    from app.providers.coding.stub_provider import StubCodingProvider

    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-GEN-BAD",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200

    def _bad(self, payload):
        return {"framework_type": "playwright", "target_test_file": "tests/smoke.spec.ts", "generated_files": []}

    monkeypatch.setattr(StubCodingProvider, "generate_patch", _bad)

    gen = client.post(f"/automation/jobs/{jid}/generate")
    assert gen.status_code == 422
    assert gen.json()["detail"]["code"] == "invalid_generated_patch"

    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["status"] == AutomationJobStatus.FAILED.value
    assert data.get("generated_patch_json") is None


def test_audit_code_generation_events(client, tmp_path: Path, db_session):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-GEN-AUD",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    client.post(f"/automation/jobs/{jid}/start")
    client.post(f"/automation/jobs/{jid}/plan")
    client.post(f"/automation/jobs/{jid}/generate")

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_CODE_GENERATION_STARTED.value in types
    assert AuditEventType.AUTOMATION_CODE_GENERATED.value in types


def _stub_execution_run_factory(*, success: bool = True, exit_code: int = 0, notes: list | None = None, **extra):
    from app.services.automation_job_service import resolve_target_test_file

    def _run(job, **kwargs):
        t = resolve_target_test_file(job) or "tests/smoke.spec.ts"
        return {
            "framework_type": "playwright",
            "command": ["npx", "playwright", "test", t],
            "target_test_file": t,
            "success": success,
            "exit_code": exit_code,
            "duration_ms": 10,
            "stdout_tail": "",
            "stderr_tail": "",
            "artifact_paths": [],
            "notes": notes or [],
            **extra,
        }

    return _run


def test_execute_job_success_moves_to_review(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-EX-OK",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200

    ex = client.post(f"/automation/jobs/{jid}/execute")
    assert ex.status_code == 200, ex.text
    assert ex.json()["status"] == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
    assert "success" in ex.json()["message"].lower()

    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["execution_result_json"] is not None
    assert data["execution_result_json"]["success"] is True


def test_execute_job_failure_exit_code(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-EX-FAIL",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(success=False, exit_code=1),
    )
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200

    ex = client.post(f"/automation/jobs/{jid}/execute")
    assert ex.status_code == 200
    assert ex.json()["status"] == AutomationJobStatus.FAILED.value
    assert "failures" in ex.json()["message"].lower() or "failed" in ex.json()["message"].lower()


def test_execute_job_wrong_state_returns_409(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-EX-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 409


def test_execute_job_prerequisites_missing_returns_400(client, tmp_path: Path, db_session, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-EX-PRE",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200

    job = db_session.get(AutomationJob, jid)
    job.framework_summary_json = {"framework_type": "cypress"}
    db_session.commit()

    ex = client.post(f"/automation/jobs/{jid}/execute")
    assert ex.status_code == 400
    assert ex.json()["detail"]["code"] == "execution_prerequisites_missing"


def test_audit_execution_events(client, tmp_path: Path, db_session, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-EX-AUD",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    client.post(f"/automation/jobs/{jid}/start")
    client.post(f"/automation/jobs/{jid}/plan")
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    client.post(f"/automation/jobs/{jid}/generate")
    client.post(f"/automation/jobs/{jid}/execute")

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_EXECUTION_STARTED.value in types
    assert AuditEventType.AUTOMATION_EXECUTION_COMPLETED.value in types


def _exec_counter_factory(first_stderr: str, second_success: bool):
    from app.services.execution_service import resolve_target_test_file

    state = {"n": 0}

    def fake(job, **kwargs):
        state["n"] += 1
        t = resolve_target_test_file(job) or "tests/smoke.spec.ts"
        base = {
            "framework_type": "playwright",
            "command": ["npx", "playwright", "test", t],
            "target_test_file": t,
            "artifact_paths": [],
            "notes": [],
        }
        if state["n"] == 1:
            return {
                **base,
                "success": False,
                "exit_code": 1,
                "duration_ms": 1,
                "stdout_tail": "",
                "stderr_tail": first_stderr,
            }
        return {
            **base,
            "success": second_success,
            "exit_code": 0 if second_success else 1,
            "duration_ms": 2,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    return fake, state


def test_repair_flow_success_after_failed_execute(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-OK",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200

    fake, state = _exec_counter_factory(
        "Timeout 5000ms exceeded waiting for locator('#x')", True
    )
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job", fake
    )
    monkeypatch.setattr("app.services.repair_service.run_playwright_execution_for_job", fake)

    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200
    assert client.get(f"/automation/jobs/{jid}").json()["status"] == AutomationJobStatus.FAILED.value

    rep = client.post(f"/automation/jobs/{jid}/repair")
    assert rep.status_code == 200, rep.text
    assert rep.json()["status"] == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
    assert state["n"] == 2

    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["failure_analysis_json"] is not None
    assert data["failure_analysis_json"].get("repairable") is True
    assert data["repair_result_json"] is not None
    assert data["repair_result_json"].get("attempted") is True
    assert data["repair_result_json"].get("reexecution_success") is True
    assert data["execution_result_json"].get("after_repair_rerun") is True


def test_repair_human_input_mailhog(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-HUMAN",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(
            success=False,
            exit_code=1,
            notes=[],
            stderr_tail="Error: connect ECONNREFUSED 127.0.0.1:8025 mailhog",
        ),
    )
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200

    rep = client.post(f"/automation/jobs/{jid}/repair")
    assert rep.status_code == 200
    assert rep.json()["status"] == AutomationJobStatus.AWAITING_HUMAN_INPUT.value
    assert "human" in rep.json()["message"].lower()

    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["repair_result_json"]["attempted"] is False


def test_repair_not_repairable_product_failure(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-NR",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(
            success=False,
            exit_code=1,
            stderr_tail="expect(locator).toBeVisible() failed",
        ),
    )
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200

    rep = client.post(f"/automation/jobs/{jid}/repair")
    assert rep.status_code == 200
    assert rep.json()["status"] == AutomationJobStatus.FAILED.value
    data = client.get(f"/automation/jobs/{jid}").json()
    assert data["failure_analysis_json"]["failure_type"] == "likely_product_failure"


def test_repair_reexecution_still_fails(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-FAIL2",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    fake, _ = _exec_counter_factory("waiting for locator", False)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job", fake
    )
    monkeypatch.setattr("app.services.repair_service.run_playwright_execution_for_job", fake)
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200

    rep = client.post(f"/automation/jobs/{jid}/repair")
    assert rep.status_code == 200
    assert rep.json()["status"] == AutomationJobStatus.FAILED.value
    body = client.get(f"/automation/jobs/{jid}").json()
    assert body["repair_result_json"]["reexecution_success"] is False


def test_repair_wrong_state_returns_409(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/repair").status_code == 409


def test_repair_missing_execution_result_returns_400(client, tmp_path: Path, db_session):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-400",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    job = db_session.get(AutomationJob, jid)
    job.status = AutomationJobStatus.FAILED.value
    job.execution_result_json = None
    job.blocked_reason = "x"
    db_session.commit()

    rep = client.post(f"/automation/jobs/{jid}/repair")
    assert rep.status_code == 400
    assert rep.json()["detail"]["code"] == "repair_prerequisites_missing"


def test_repair_second_call_returns_409(client, tmp_path: Path, monkeypatch):
    """Second /repair after a completed repair flow is rejected."""
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-2X",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(
            success=False,
            exit_code=1,
            stderr_tail="Error: connect ECONNREFUSED 127.0.0.1:8025 mailhog",
        ),
    )
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/repair").status_code == 200
    r2 = client.post(f"/automation/jobs/{jid}/repair")
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "repair_already_attempted"


def test_audit_repair_events(client, tmp_path: Path, db_session, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REP-AUD",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = uuid.UUID(r.json()["id"])
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    fake, _ = _exec_counter_factory("waiting for locator", True)
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job", fake
    )
    monkeypatch.setattr("app.services.repair_service.run_playwright_execution_for_job", fake)
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/repair").status_code == 200

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_FAILURE_ANALYZED.value in types
    assert AuditEventType.AUTOMATION_REPAIR_STARTED.value in types
    assert AuditEventType.AUTOMATION_REPAIR_APPLIED.value in types
    assert AuditEventType.AUTOMATION_REEXECUTION_COMPLETED.value in types


def _patch_playwright_run_for_job_and_review(monkeypatch, fake):
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        fake,
    )
    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        fake,
    )


def _job_at_awaiting_review(client, tmp_path: Path, monkeypatch, *, approved_case_id: str = "CASE-RVW"):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": approved_case_id,
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke",
            "steps": ["open app"],
        },
    )
    jid = r.json()["id"]
    _patch_playwright_run_for_job_and_review(monkeypatch, _stub_execution_run_factory())
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200
    return jid


def test_approve_automation_job_for_pr(client, tmp_path: Path, monkeypatch, db_session):
    jid = uuid.UUID(_job_at_awaiting_review(client, tmp_path, monkeypatch, approved_case_id="CASE-APR"))
    ap = client.post(f"/automation/jobs/{jid}/approve", json={"actor_id": "qa.lead"})
    assert ap.status_code == 200
    assert ap.json()["status"] == AutomationJobStatus.APPROVED_FOR_PR.value
    assert "approved" in ap.json()["message"].lower()

    rows = db_session.execute(
        select(AutomationJobReviewAction).where(AutomationJobReviewAction.automation_job_id == jid)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].action_type == AutomationJobReviewActionType.APPROVE.value
    assert rows[0].actor_id == "qa.lead"

    audits = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    assert AuditEventType.AUTOMATION_REVIEW_APPROVED.value in {x.event_type for x in audits}


def test_approve_wrong_state_returns_409(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-APR-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert client.post(f"/automation/jobs/{jid}/approve", json={"actor_id": "x"}).status_code == 409


def test_request_revision_success_returns_to_review(client, tmp_path: Path, monkeypatch):
    jid = _job_at_awaiting_review(client, tmp_path, monkeypatch, approved_case_id="CASE-REV-OK")
    rev = client.post(
        f"/automation/jobs/{jid}/request-revision",
        json={
            "actor_id": "qa.lead",
            "instruction_text": "Prefer auth fixture over inline login.",
        },
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["status"] == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
    assert "passed" in rev.json()["message"].lower() or "revision" in rev.json()["message"].lower()


def test_request_revision_human_input_after_rerun(client, tmp_path: Path, monkeypatch):
    from app.services.execution_service import resolve_target_test_file

    state = {"n": 0}

    def fake(job, **kwargs):
        state["n"] += 1
        t = resolve_target_test_file(job) or "tests/smoke.spec.ts"
        base = {
            "framework_type": "playwright",
            "command": ["npx", "playwright", "test", t],
            "target_test_file": t,
            "artifact_paths": [],
            "notes": [],
        }
        if state["n"] == 1:
            return {**base, "success": True, "exit_code": 0, "duration_ms": 1}
        return {
            **base,
            "success": False,
            "exit_code": 1,
            "duration_ms": 1,
            "stderr_tail": "Error: connect ECONNREFUSED 127.0.0.1:8025 mailhog",
        }

    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REV-HUM",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke",
            "steps": ["x"],
        },
    )
    jid = r.json()["id"]
    _patch_playwright_run_for_job_and_review(monkeypatch, fake)
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200

    rev = client.post(
        f"/automation/jobs/{jid}/request-revision",
        json={"actor_id": "qa.lead", "instruction_text": "Adjust OTP handling."},
    )
    assert rev.status_code == 200
    assert rev.json()["status"] == AutomationJobStatus.AWAITING_HUMAN_INPUT.value
    assert "human" in rev.json()["message"].lower()


def test_request_revision_execution_still_fails(client, tmp_path: Path, monkeypatch):
    from app.services.execution_service import resolve_target_test_file

    state = {"n": 0}

    def fake(job, **kwargs):
        state["n"] += 1
        t = resolve_target_test_file(job) or "tests/smoke.spec.ts"
        base = {
            "framework_type": "playwright",
            "command": ["npx", "playwright", "test", t],
            "target_test_file": t,
            "artifact_paths": [],
            "notes": [],
        }
        if state["n"] == 1:
            return {**base, "success": True, "exit_code": 0, "duration_ms": 1}
        return {
            **base,
            "success": False,
            "exit_code": 1,
            "duration_ms": 1,
            "stderr_tail": "expect(locator).toBeVisible() failed",
        }

    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REV-FAIL",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke",
            "steps": ["x"],
        },
    )
    jid = r.json()["id"]
    _patch_playwright_run_for_job_and_review(monkeypatch, fake)
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200

    rev = client.post(
        f"/automation/jobs/{jid}/request-revision",
        json={"actor_id": "qa.lead", "instruction_text": "Tweak assertion."},
    )
    assert rev.status_code == 200
    assert rev.json()["status"] == AutomationJobStatus.FAILED.value


def test_request_revision_wrong_state_returns_409(client, tmp_path: Path):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-REV-409",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
        },
    )
    jid = r.json()["id"]
    assert (
        client.post(
            f"/automation/jobs/{jid}/request-revision",
            json={"actor_id": "a", "instruction_text": "do something"},
        ).status_code
        == 409
    )


def test_request_revision_empty_instruction_returns_422(client, tmp_path: Path, monkeypatch):
    jid = _job_at_awaiting_review(client, tmp_path, monkeypatch, approved_case_id="CASE-REV-422")
    rev = client.post(
        f"/automation/jobs/{jid}/request-revision",
        json={"actor_id": "qa.lead", "instruction_text": ""},
    )
    assert rev.status_code == 422


def test_manual_edit_ack_success(client, tmp_path: Path, monkeypatch):
    jid = _job_at_awaiting_review(client, tmp_path, monkeypatch, approved_case_id="CASE-MAN-OK")
    ack = client.post(
        f"/automation/jobs/{jid}/manual-edit-ack",
        json={"actor_id": "qa.lead", "note": "Fixed selectors locally."},
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value


def test_manual_edit_ack_from_failed_execute(client, tmp_path: Path, monkeypatch):
    _playwright_fixture_repo(tmp_path)
    r = client.post(
        "/automation/jobs",
        json={
            "approved_case_id": "CASE-MAN-FAIL",
            "requested_by": "runner",
            "repo_path": str(tmp_path.resolve()),
            "case_title": "Smoke",
            "steps": ["x"],
        },
    )
    jid = r.json()["id"]
    monkeypatch.setattr(
        "app.services.automation_job_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(success=False, exit_code=1),
    )
    assert client.post(f"/automation/jobs/{jid}/start").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/plan").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/generate").status_code == 200
    assert client.post(f"/automation/jobs/{jid}/execute").status_code == 200

    monkeypatch.setattr(
        "app.services.automation_review_service.run_playwright_execution_for_job",
        _stub_execution_run_factory(),
    )
    ack = client.post(
        f"/automation/jobs/{jid}/manual-edit-ack",
        json={"actor_id": "qa.lead", "note": "Re-ran after manual fix."},
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value


def test_audit_review_revision_events(client, tmp_path: Path, db_session, monkeypatch):
    jid = uuid.UUID(_job_at_awaiting_review(client, tmp_path, monkeypatch, approved_case_id="CASE-RV-AUD"))
    assert (
        client.post(
            f"/automation/jobs/{jid}/request-revision",
            json={"actor_id": "aud", "instruction_text": "Minor tweak."},
        ).status_code
        == 200
    )

    rows = db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "automation_job",
            AuditLog.entity_id == str(jid),
        )
    ).scalars().all()
    types = {x.event_type for x in rows}
    assert AuditEventType.AUTOMATION_REVIEW_REVISION_REQUESTED.value in types
    assert AuditEventType.AUTOMATION_REVIEW_REVISION_APPLIED.value in types
    assert AuditEventType.AUTOMATION_REEXECUTION_COMPLETED.value in types
