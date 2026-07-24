#!/usr/bin/env python3
"""Export stable /api/v1 JSON fixtures for frontend contract testing.

Usage (from backend/):
    python scripts/export_ui_api_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

# Ensure backend package is importable
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("JIRA_USE_STUB", "true")
os.environ.setdefault("JIRA_BASE_URL", "https://usfoods.atlassian.net")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, pool  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.db.models  # noqa: F401
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402

FIXTURES_DIR = BACKEND_ROOT / "docs" / "api-fixtures"


def _write(name: str, payload: object) -> None:
    path = FIXTURES_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(BACKEND_ROOT)}")


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=pool.StaticPool,
    )
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()

    def _db_override():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _db_override
    get_settings.cache_clear()

    with TestClient(app) as client:
        # Stories
        _write("stories-list.json", client.get("/api/v1/stories?projectKey=NSP").json())
        _write("story-detail.json", client.get("/api/v1/stories/NSP-696").json())

        # Sprint 1 workspace stages
        create = client.post(
            "/api/v1/stories/NSP-FIXTURE-696/test-design-runs",
            json={"initiatedBy": "qa-lead"},
        )
        run_id = create.json()["id"]
        _write("test-design-run-intake-ready.json", client.get(f"/api/v1/test-design-runs/{run_id}").json())

        client.post(f"/api/v1/test-design-runs/{run_id}/analyze")
        _write("test-design-run-analysis-ready.json", client.get(f"/api/v1/test-design-runs/{run_id}").json())
        _write("requirement-analysis.json", client.get(f"/api/v1/test-design-runs/{run_id}/analysis").json())

        client.post(f"/api/v1/test-design-runs/{run_id}/prepare-plan")
        _write("test-design-run-awaiting-plan-approval.json", client.get(f"/api/v1/test-design-runs/{run_id}").json())
        _write("test-design-plan.json", client.get(f"/api/v1/test-design-runs/{run_id}/plan").json())

        client.post(f"/api/v1/test-design-runs/{run_id}/approve-plan")
        _write("test-design-run-plan-approved.json", client.get(f"/api/v1/test-design-runs/{run_id}").json())

        client.post(f"/api/v1/test-design-runs/{run_id}/generate-test-cases")
        _write(
            "test-design-run-awaiting-test-case-review.json",
            client.get(f"/api/v1/test-design-runs/{run_id}").json(),
        )
        _write("test-design-review-data.json", client.get(f"/api/v1/test-design-runs/{run_id}/review-data").json())

        client.post(
            f"/api/v1/test-design-runs/{run_id}/approve",
            json={"actorId": "reviewer", "notes": "approved"},
        )
        _write("test-design-run-approved.json", client.get(f"/api/v1/test-design-runs/{run_id}").json())

        pub = client.post(f"/api/v1/test-design-runs/{run_id}/publish")
        if pub.status_code == 200:
            _write("test-design-run-automation-ready.json", pub.json())

        # Legacy run
        legacy = client.post("/workflow/runs", json={"jira_issue_key": "QSW-LEGACY", "initiated_by": "tester"})
        legacy_id = legacy.json()["id"]
        client.post(f"/workflow/runs/{legacy_id}/start")
        _write("test-design-run-legacy.json", client.get(f"/api/v1/test-design-runs/{legacy_id}").json())

        # Test cases
        _write("test-case-list.json", client.get("/api/v1/test-cases?status=automation_ready").json())
        records = client.get(f"/api/v1/test-cases?workflowRunId={run_id}").json().get("items") or []
        if records:
            _write("test-case-detail.json", client.get(f"/api/v1/test-cases/{records[0]['id']}").json())

        # Sprint 2 session
        tmp_repo = BACKEND_ROOT / "docs" / "api-fixtures" / "_tmp_repo"
        tmp_repo.mkdir(parents=True, exist_ok=True)
        sess = client.post(
            "/api/v1/sessions",
            json={
                "approvedCaseId": "NSP-TC-01",
                "engine": "stub",
                "createdBy": "qa",
                "repoPath": str(tmp_repo.resolve()),
                "steps": ["Open app"],
            },
        )
        if sess.status_code == 201:
            sid = sess.json()["id"]
            _write("automation-session-draft.json", sess.json())
            _write("automation-brief.json", client.get(f"/api/v1/sessions/{sid}/brief").json())
            try:
                prep = client.post(f"/api/v1/sessions/{sid}/prepare-plan", json={"actorId": "qa"})
                if prep.status_code == 200:
                    _write("automation-session-plan-ready.json", prep.json())
                else:
                    print(f"skip automation-session-plan-ready (status {prep.status_code})")
            except Exception as exc:  # noqa: BLE001
                print(f"skip automation-session-plan-ready ({exc})")

        # Platform
        _write("dashboard.json", client.get("/api/v1/dashboard").json())
        _write("settings.json", client.get("/api/v1/settings").json())
        _write("error-not-found.json", client.get(f"/api/v1/test-design-runs/{uuid.uuid4()}").json())
        bad = client.post(f"/api/v1/test-design-runs/{run_id}/generate-test-cases")
        if bad.status_code >= 400:
            _write("error-invalid-state.json", bad.json())

    # OpenAPI slice (always written)
    openapi = app.openapi()
    ui_paths = {k: v for k, v in openapi.get("paths", {}).items() if k.startswith("/api/v1")}
    ui_openapi = {**openapi, "paths": ui_paths}
    (BACKEND_ROOT / "docs" / "openapi-ui-v1.json").write_text(
        json.dumps(ui_openapi, indent=2) + "\n",
        encoding="utf-8",
    )
    print("wrote docs/openapi-ui-v1.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"fixture export incomplete: {exc}", file=sys.stderr)
        # still attempt openapi export
        from app.main import app as _app

        openapi = _app.openapi()
        ui_paths = {k: v for k, v in openapi.get("paths", {}).items() if k.startswith("/api/v1")}
        (BACKEND_ROOT / "docs" / "openapi-ui-v1.json").write_text(
            json.dumps({**openapi, "paths": ui_paths}, indent=2) + "\n",
            encoding="utf-8",
        )
        raise
