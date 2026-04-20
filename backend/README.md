# Qswarm backend (Sprint 1 + Sprint 2 partial)

Backend service for an agentic QA workflow: read Jira stories, normalize intake, run a LangGraph shell, produce a draft test design, pause for human approval, and persist state, artifacts, approvals, and audit logs in PostgreSQL.

**Sprint 2:** internal **`AutomationJob`** and `/automation/jobs`. **`repo_path`** must be a **local directory** the API can read (no clone). **`POST .../start`** runs: (1) **framework scan** (Playwright vs unknown), (2) deterministic **case enhancement** into **`case_spec_json`** (optional case fields on create are stored in **`case_input_json`**), (3) **repo context** curation into **`repo_context_json`** for Playwright. Unknown framework → **`failed`** with summary; invalid `repo_path` → **400**. Successful Playwright run ends in **`planning_changes`**. From **`planning_changes`**, **`POST .../plan`** runs the **change planning** layer (default **`CODING_PROVIDER=stub`**: deterministic heuristics, no external LLM), validates a structured **`change_plan_json`**, and on success moves the job to **`generating_code`**. From **`generating_code`**, **`POST .../generate`** runs **stub patch generation**, **patch validation** against the stored plan, **workspace apply** under **`repo_path`** (no git worktrees), persists **`generated_patch_json`** (metadata + apply summary, not full file bodies), and moves the job to **`executing`**. From **`executing`**, **`POST .../execute`** runs **`npx playwright test <target>`** (argv list, no shell, configurable timeout) in the repo workspace, persists bounded **`execution_result_json`** (stdout/stderr tails, exit code, timing), then **`awaiting_automation_review`** if the process exits **0**, or **`failed`** on non-zero exit, timeout, launch error, or missing target file. After a **failed** run (with execution results and plan in place), **`POST .../repair`** runs **one** deterministic **failure analysis** (stub-friendly heuristics on tails/exit code), stores **`failure_analysis_json`**, then either **`awaiting_human_input`** when the environment or data is unclear, stays **`failed`** when not auto-repairable, or applies **at most one** stub repair patch (paths must stay within the original plan’s create/modify lists), re-executes Playwright **once**, stores **`repair_result_json`**, and ends in **`awaiting_automation_review`** if the re-run passes or **`failed`** if it still fails. A second **`/repair`** on the same job returns **409** `repair_already_attempted`. From **`awaiting_automation_review`**, reviewers can **`POST .../approve`** (body `actor_id`) to move the job to **`approved_for_pr`** (PR creation is **not** implemented yet — this is readiness only). They can **`POST .../request-revision`** with `actor_id` and `instruction_text` to record a review action, enter **`revising_after_review`**, run the stub (or future LLM) **`revise_after_review`** pipeline (**`validate_repair_patch`** scope: plan create/modify paths only), apply the patch, re-run Playwright once, then land in **`awaiting_automation_review`** on pass, **`awaiting_human_input`** when post-run failure analysis indicates missing env/data, or **`failed`** otherwise. **`POST .../manual-edit-ack`** (with `actor_id` and `note`) skips the provider and re-executes only — allowed from **`awaiting_automation_review`** or **`failed`** when execution prerequisites still hold. Human review steps are persisted in **`automation_job_review_actions`** (migration **`20260408_0007_automation_job_review_actions`**). Run **`alembic upgrade head`** — migrations include **`generated_patch_json`**, **`execution_result_json`**, **`failure_analysis_json`**, **`repair_result_json`**, and the review-actions table (see **`20260408_0006`** / **`20260408_0007`** among others).

## Stack

- Python 3.11+
- FastAPI, Uvicorn, Pydantic, pydantic-settings
- SQLAlchemy 2.0, Alembic, PostgreSQL (production)
- LangGraph, httpx, python-dotenv, structlog, pytest

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set at least `DATABASE_URL` for PostgreSQL.

## Database migrations

With PostgreSQL running and `DATABASE_URL` set:

```bash
alembic upgrade head
```

## Run the app

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/docs` for interactive API docs.

## Tests

Tests use an in-memory SQLite database and stub Jira (see `tests/conftest.py`).

```bash
pytest tests/ -v
```

## Example API flow (Sprint 1)

1. `POST /workflow/runs` with body `{"jira_issue_key": "PROJ-1", "initiated_by": "you"}`  
2. `POST /workflow/runs/{run_id}/start` — fetches Jira (or stub), stores story, runs intake + test design agents, creates an approval; run status becomes `awaiting_approval`.  
3. `GET /approvals/{approval_id}` — inspect the pending approval.  
4. `POST /approvals/{approval_id}/approve` or `.../reject` with `{"actor_id": "reviewer", "notes": "..."}`.  
5. `GET /audit/workflow/{run_id}` — inspect the audit trail.

Other useful endpoints: `GET /health`, `GET /jira/connection-test`, `GET /jira/issues/{issue_key}`, `GET /jira/story/{issue_key}`, `POST /jira/search`, `POST /intake/from-jira/{issue_key}`.

**Manual real Jira check:** set `JIRA_USE_STUB=false` and valid `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` in `.env`, restart the API, open `/docs`, call `GET /jira/connection-test` (uses sample issue `NSP-677`), then `GET /jira/issues/NSP-677` for a normalized issue payload (no raw Jira JSON).

## Automation jobs (Sprint 2)

- `POST /automation/jobs` — optional case hints: `case_title`, `case_description`, `preconditions`, `steps`, `expected_results` (stored until start; used to build **`case_spec_json`**). Optional **`repo_owner`** / **`repo_name`** (GitHub `owner/repo` for later **`/create-pr`**; defaults can also come from env).
- `GET /automation/jobs/{job_id}` — includes `framework_summary_json`, **`case_spec_json`**, **`repo_context_json`**, `framework_type`, `change_plan_json`, **`generated_patch_json`**, **`execution_result_json`**, **`failure_analysis_json`**, **`repair_result_json`**, `repo_owner`, `repo_name`, `final_result_json` when set
- `GET /automation/jobs` — newest first
- `POST /automation/jobs/{job_id}/start` — requires valid **`repo_path`**. Playwright: scan + case spec + bounded repo context → **`planning_changes`**. Unknown: **`failed`** (HTTP **200**). Bad path: **400**.
- `POST /automation/jobs/{job_id}/plan` — only from **`planning_changes`** with all of **`framework_summary_json`**, **`case_spec_json`**, **`repo_context_json`** present. Produces **`change_plan_json`** and **`generating_code`**, or **422** if the plan fails validation (job → **`failed`**). Wrong state: **409**. Missing inputs: **400**.
- `POST /automation/jobs/{job_id}/generate` — only from **`generating_code`** with plan + context + **`repo_path`**. Stub provider returns full-file entries; QSwarm validates scope vs **`change_plan_json`**, writes files under **`repo_path`**, stores **`generated_patch_json`**, then **`executing`**. Validation/apply failure → **`failed`** (**422** with `invalid_generated_patch` or `workspace_apply_failed`). Wrong state: **409**. Missing inputs: **400**.
- `POST /automation/jobs/{job_id}/execute` — only from **`executing`**, Playwright-only. Target file: **`change_plan_json.target_test_file`**, else **`generated_patch_json.target_test_file`**. Wrong state: **409**. Missing prerequisites (e.g. not Playwright summary, missing target, bad **`repo_path`**) → **400** `execution_prerequisites_missing`. HTTP **200** after a completed run attempt (pass → **`awaiting_automation_review`**, fail → **`failed`** with **`execution_result_json`**).
- `POST /automation/jobs/{job_id}/repair` — only when status is **`failed`**, **`execution_result_json`** is present (failed run), and **`repair_result_json`** is still unset. **409** if the job is not **`failed`** or repair was already completed. **400** `repair_prerequisites_missing` if plan/framework/repo prerequisites are missing. **200** when the flow finishes in **`awaiting_automation_review`**, **`awaiting_human_input`**, or **`failed`** (with **`failure_analysis_json`** / **`repair_result_json`** updated as appropriate).
- `POST /automation/jobs/{job_id}/approve` — only from **`awaiting_automation_review`**. Body: **`actor_id`**. Persists an **`approve`** review action; status → **`approved_for_pr`**. Wrong state: **409**.
- `POST /automation/jobs/{job_id}/request-revision` — only from **`awaiting_automation_review`**. Body: **`actor_id`**, **`instruction_text`**. Persists **`request_revision`**, runs revision patch + one re-execution; final status **`awaiting_automation_review`**, **`awaiting_human_input`**, or **`failed`**. **400** if execution prerequisites are missing; wrong state: **409**.
- `POST /automation/jobs/{job_id}/manual-edit-ack` — from **`awaiting_automation_review`** or **`failed`** (with valid Playwright/repo/plan context). Body: **`actor_id`**, **`note`**. Persists **`manual_edit_ack`** and re-runs tests only (no coding provider). **400** / **409** analogous to revision where applicable.
- `POST /automation/jobs/{job_id}/create-pr` — only from **`approved_for_pr`**. Runs a **pre-PR** flow on the **local** repo at **`repo_path`**: verify git, ensure **`qswarm/…`** branch, **fetch** + **merge** latest **`base_branch`** (merge, not rebase). **Merge conflict** → job **`awaiting_human_input`**, a **`pr_records`** row with **`base_refresh_conflict`**, audit **`automation_base_refresh_conflict`** — no commit/PR. If refresh did not change the branch and the last execution was already passing, **Playwright is not re-run**; if the merge updated the branch, **tests run again** before commit. Then **stage + commit + `git push`** and **GitHub REST** `POST /repos/{owner}/{repo}/pulls` (requires **`GITHUB_TOKEN`** and owner/repo on the job or **`GITHUB_DEFAULT_*`** env). Success → job **`pr_created`** and **`pr_records`** row **`pr_created`** with **PR URL/number**. Other git/GitHub failures → **`failed`**. Response includes **`pr_url`** / **`pr_number`** when created. **Merge remains manual** — QSwarm does not merge PRs. Migration **`20260408_0008_pr_records_and_github_repo_fields`**.

## Configuration

| Variable | Purpose |
|----------|---------|
| `APP_NAME`, `APP_ENV`, `APP_DEBUG` | App metadata / logging |
| `DATABASE_URL` | SQLAlchemy URL (`postgresql+psycopg://...` recommended) |
| `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Live Jira (Basic auth) |
| `JIRA_USE_STUB` | When `true` or token missing, Jira calls use stub data |
| `INTERNAL_ACTOR_DEFAULT` | Placeholder default actor (Sprint 1) |
| `CODING_PROVIDER` | Change planning provider (`stub` default; real Codex/Claude later) |
| `PLAYWRIGHT_EXECUTION_TIMEOUT_SECONDS` | Subprocess timeout for `POST .../execute` (default **120**) |
| `GITHUB_TOKEN` | PAT for `POST .../create-pr` (repo scope: contents + pull requests) |
| `GITHUB_DEFAULT_REPO_OWNER`, `GITHUB_DEFAULT_REPO_NAME` | Fallback GitHub repo when job has no `repo_owner` / `repo_name` |
| `GITHUB_API_BASE_URL` | GitHub API host (default `https://api.github.com`) |
