#!/usr/bin/env python3
"""
True end-to-end live check against a running API + DATABASE_URL.

Prerequisites:
  1. backend/.env with DATABASE_URL (e.g. Render Postgres)
  2. API up:  uvicorn app.main:app --host 127.0.0.1 --port 8765
  3. Optional: E2E_BASE_URL=http://127.0.0.1:8765

Exits 0 only if every step passes (HTTP + DB assertions).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def _db():
    from app.core.config import get_settings

    get_settings.cache_clear()
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def _approval_id_for_run(eng, run_id: uuid.UUID) -> uuid.UUID:
    with eng.connect() as c:
        row = c.execute(
            text("SELECT id FROM approvals WHERE workflow_run_id = :r ORDER BY created_at LIMIT 1"),
            {"r": str(run_id)},
        ).first()
        if not row:
            raise SystemExit(f"No approval row for run {run_id}")
        return row[0]


def _assert_db_after_start(eng, run_id: uuid.UUID, issue_key: str) -> None:
    with eng.connect() as c:
        wr = c.execute(
            text(
                "SELECT status, jira_story_id, graph_state_json FROM workflow_runs WHERE id = :id"
            ),
            {"id": str(run_id)},
        ).mappings().first()
        assert wr, "workflow_run missing"
        assert wr["status"] == "awaiting_approval", wr["status"]
        assert wr["jira_story_id"], "jira_story_id not set"

        js = c.execute(
            text("SELECT issue_key FROM jira_stories WHERE id = :id"),
            {"id": str(wr["jira_story_id"])},
        ).scalar_one()
        assert js.upper() == issue_key.upper(), (js, issue_key)

        n_intake = c.execute(
            text(
                "SELECT COUNT(*) FROM agent_artifacts WHERE workflow_run_id = :r AND artifact_type = 'story_intake'"
            ),
            {"r": str(run_id)},
        ).scalar()
        assert int(n_intake) == 1, n_intake

        n_td = c.execute(
            text(
                "SELECT COUNT(*) FROM agent_artifacts WHERE workflow_run_id = :r AND artifact_type = 'test_design'"
            ),
            {"r": str(run_id)},
        ).scalar()
        assert int(n_td) == 1, n_td

        n_appr = c.execute(
            text("SELECT COUNT(*) FROM approvals WHERE workflow_run_id = :r AND status = 'pending'"),
            {"r": str(run_id)},
        ).scalar()
        assert int(n_appr) == 1, n_appr

        n_audit = c.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE workflow_run_id = :r"),
            {"r": str(run_id)},
        ).scalar()
        assert int(n_audit) >= 5, f"expected >=5 audit rows, got {n_audit}"


def main() -> int:
    base = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    eng = _db()

    with httpx.Client(base_url=base, timeout=120.0) as client:
        h = client.get("/health")
        assert h.status_code == 200, h.text
        assert h.json().get("status") == "ok"

        # --- Approve path ---
        key_a = f"E2E-APR-{uuid.uuid4().hex[:8].upper()}"
        r = client.post(
            "/workflow/runs",
            json={"jira_issue_key": key_a, "initiated_by": "e2e_script"},
        )
        assert r.status_code == 201, r.text
        run_a = uuid.UUID(r.json()["id"])

        r = client.post(f"/workflow/runs/{run_a}/start")
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "awaiting_approval"

        _assert_db_after_start(eng, run_a, key_a)

        aid_a = _approval_id_for_run(eng, run_a)
        r = client.post(
            f"/approvals/{aid_a}/approve",
            json={"actor_id": "e2e_reviewer", "notes": "approved in e2e"},
        )
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "approved"

        with eng.connect() as c:
            st = c.execute(
                text("SELECT status FROM workflow_runs WHERE id = :id"),
                {"id": str(run_a)},
            ).scalar_one()
        assert st == "completed", st

        # --- Reject path ---
        key_r = f"E2E-REJ-{uuid.uuid4().hex[:8].upper()}"
        r = client.post(
            "/workflow/runs",
            json={"jira_issue_key": key_r, "initiated_by": "e2e_script"},
        )
        assert r.status_code == 201, r.text
        run_r = uuid.UUID(r.json()["id"])

        r = client.post(f"/workflow/runs/{run_r}/start")
        assert r.status_code == 200, r.text

        _assert_db_after_start(eng, run_r, key_r)

        aid_r = _approval_id_for_run(eng, run_r)
        r = client.post(
            f"/approvals/{aid_r}/reject",
            json={"actor_id": "e2e_reviewer", "notes": "rejected in e2e"},
        )
        assert r.status_code == 200, r.text
        assert r.json().get("status") == "rejected"

        with eng.connect() as c:
            st = c.execute(
                text("SELECT status FROM workflow_runs WHERE id = :id"),
                {"id": str(run_r)},
            ).scalar_one()
        assert st == "rejected", st

    print("E2E OK: create → start → jira + artifacts + approval + audit → approve → completed")
    print("E2E OK: create → start → reject → run rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
