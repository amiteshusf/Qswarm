# Qswarm backend (Sprint 1 + Sprint 2 partial)

Backend service for an agentic QA workflow: read Jira stories, normalize intake, run a LangGraph shell, produce a draft test design, pause for human approval, and persist state, artifacts, approvals, and audit logs in PostgreSQL.

**UI-facing API (`/api/v1`):** The **QSwarm Web** frontend should call **`/api/v1/...`** (camelCase JSON, aggregated shapes such as **`GET /api/v1/dashboard`** and **`GET /api/v1/sessions/{id}`** with embedded rounds/patches/executions). These routes are a thin BFF on top of the same services as the legacy paths below; they do not replace Sprint 1/2 internals. Legacy routes (**`/automation/sessions`**, **`/repo-connections`**, etc.) remain supported for scripts and backward compatibility. **`GET /api/v1/dashboard`** is intentionally **normalized for the Web Zod contract** (e.g. `sessionCounts` as a fixed record with underscore keys, `recentSessions[].status` mapped from internal session/job states, string defaults for nullable ids/refs)—not a raw dump of internal models.

**Sprint 2 control plane (session layer):** **`AutomationSession`** is the top-level orchestration record (1:1 with an **`AutomationJob`** in v1). Use **`POST /automation/sessions`** to create session + job, then **`POST /automation/sessions/{id}/start`** to run the scan → plan → generate → execute pipeline via a pluggable **coding engine** adapter.

- **Coding engines:** `coding_engine` must be one of **`stub`**, **`claude_code`**, **`copilot_agent`**. **`stub`** is fully wired (delegates to existing `automation_job_service` / `CODING_PROVIDER=stub`). **`claude_code`** runs a **local CLI subprocess** (no shell): after QSwarm builds the validated **`change_plan_json`**, the adapter invokes **`QSWARM_CLAUDE_CODE_COMMAND`** with optional **`QSWARM_CLAUDE_CODE_EXTRA_ARGS`**, then assembles **`generated_patch_json`** by reading plan-scoped files from the workspace so existing patch validation / apply / Playwright execution unchanged. Configure **`QSWARM_CLAUDE_CODE_ENABLED=true`**, a resolvable command (e.g. `claude` or absolute path), and timeouts; see `.env.example`. Failures surface as typed adapter errors (**`EngineConfigurationError`**, **`EngineRepoAccessError`**, **`EngineTimeoutError`**, **`EngineMalformedOutputError`**, **`EngineAdapterError`**) and HTTP **400** / **422** / **502** / **504** on session routes where applicable. **`copilot_agent`** runs the **GitHub Copilot CLI** locally the same way (argv from **`QSWARM_COPILOT_AGENT_COMMAND`** + **`QSWARM_COPILOT_AGENT_EXTRA_ARGS`** + **`-p`** + prompt); CLI stdout/stderr are treated as opaque — patch truth is **workspace files on disk** after the run. Copilot **SDK / cloud-agent** integration is **not** in scope yet. Manual rerun for both external engines **does not** call the CLI again (Playwright re-exec only).

- **APIs:** Revision rounds, plan/patch versions, execution attempts, and review requests (**`GET .../rounds`**, **`.../plan-versions`**, **`.../patch-versions`**, **`.../execution-attempts`**, **`.../review-requests`**). **`POST .../request-revision`**, **`.../manual-edit-ack`**, and **`.../approve`** mirror the job-level review loop while writing session history. **Session PR creation (Sprint 2):** register reusable remotes via **`POST /repo-connections`** (provider enum: `github` \| `gitlab` \| `bitbucket` \| `azure_devops`; only **GitHub** is implemented). Optional **`POST|GET|PATCH /repo-connections/{id}/branch-policy`** sets defaults (`base_branch_default`, `branch_naming_pattern` with `{session_id}` / `{job_id}` / `{approved_case_id}`, PR title/body templates). From **`approved_for_pr`**, **`POST /automation/sessions/{id}/create-pr`** (body: `actor_id`, `repository_connection_id`, optional branch/title overrides) runs **`creating_pr`** → **`pr_created`** and persists **`code_review_requests`** (normalized PR metadata). Failures land in **`pr_creation_failed`** / session **`pr_failed`**. **`GET .../code-review-requests`** lists attempts. Git / GitHub calls use the same safe argv patterns as job **`/create-pr`**; GitLab / Bitbucket / Azure DevOps adapters are placeholders. Migration: **`20260421_0012_automation_session_control_plane`**, **`20260512_0013_repository_connections_session_pr`**, **`20260518_0014_automation_session_repository_connection`**.
- **Hosted workspace & framework-aware execution prep (Sprint 2):** For **`POST /automation/sessions/{id}/start`**, if **`repo_path`** is unset or does not exist on the server, QSwarm can **clone** into a deterministic managed directory (**`QSWARM_WORKSPACE_ROOT`**, default **`/tmp/qswarm/sessions/<session_id>/repo`**) using optional **`repository_connection_id`**, **`GITHUB_TOKEN`**, and **`session.base_branch`**. **Hosted materialized** workspaces then follow a generic pipeline implemented in **`app/services/framework_runtime_service.py`**: **(1)** filesystem **framework/runtime detection** (**`FrameworkRuntimeProfile`**), **(2)** **bootstrap plan** (**`RepoBootstrapPlan`** — e.g. **`npm ci`** / **`npm install`** for Node), **(3)** dependency bootstrap via existing **`repo_bootstrap_service`**, **(4)** **runtime validation** (**`RuntimeValidationResult`**) including **`node_modules/@playwright/test`** checks after npm, **(5)** for **Playwright** only, **`npx playwright install chromium`** (explicit argv, repo root cwd, **`QSWARM_PLAYWRIGHT_BROWSER_INSTALL_TIMEOUT_SECONDS`**) before any coding-engine / Playwright test execution. **WebdriverIO**, **Cypress**, **Maven**, **Gradle**, **pytest/Python**, and **unknown** layouts are **detected** but return **`hosted_framework_not_supported`** (HTTP **400**) until wired — this avoids QSwarm becoming Playwright-only by accident while keeping failures explicit. **`local_existing`** workspaces keep the previous npm-only bootstrap (with optional **`QSWARM_SKIP_BOOTSTRAP_IF_NODE_MODULES`**); they do not run the strict hosted detection gate or Playwright browser install. At **Playwright execution** time, **`execution_service.build_playwright_execution_plan`** produces an **`ExecutionPlan`** (cwd + argv) so the runner is not hard-coded ad hoc. Typed errors: **`FrameworkDetectionError`**, **`UnsupportedHostedFrameworkError`**, **`RuntimeValidationError`**, **`PlaywrightBrowserPreparationError`** (with **`RepoBootstrapError`**, subclasses **`HostedExecutionPreparationError`** for API mapping), **`ExecutionPlanError`**. Successful hosted runs still require the **coding engine CLI** when not using **`stub`**.
- **Branch policy PR templates:** If **`pr_title_template`** / **`pr_body_template`** are set, **`POST /automation/sessions/{id}/create-pr`** renders them (single-brace placeholders, `string.Formatter` rules including `{{` / `}}` for literals) in the provider-agnostic orchestration layer before the adapter runs and before **`code_review_requests`** rows are written. Supported names: **`session_id`**, **`approved_case_id`**, **`coding_engine`**, **`source_reference`**, **`job_id`**, **`repo_name`**, **`owner_or_org`**, **`target_branch`**, **`source_branch`**. Unknown names or unsupported syntax return **400** (for example **`pr_template_invalid_placeholder`**). When a template field is null, title/body use the existing built-in generator (`build_pr_title_and_body`). Optional **`title_override`** / **`body_override`** on create-pr are still treated as literal strings (not templated).

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
2. `POST /workflow/runs/{run_id}/start` — fetches Jira (or stub), story intake, test design, **publishes one linked Jira draft review Task** (summary `Draft Test Design: {PARENT-KEY}`) for human-readable review, then creates the QSwarm approval; run status becomes `awaiting_approval` (or `failed` if Jira issue creation fails).  
3. `GET /workflow/runs/{run_id}/jira-review` — linked review issue metadata (`jira_test_design_review_issues`). Legacy **`GET .../generated-test-cases`** remains for old rows but new Sprint 1 runs do not populate multi-case draft Tasks.  
4. **Comment-driven review (Sprint 1):** Reviewers comment on the Jira review issue with **`@QSwarm ...`**. For v1 there is **no webhook** — call **`POST /workflow/runs/{run_id}/jira-review/process-comments`** to fetch Jira comments, process each `@QSwarm` line in order, bump **internal** versions (`test_design_versions` / `test_design_feedback`), and post a **delta-only** reply comment on the same issue. **`GET .../jira-review/comments`** returns persisted processing history. **API refine/regenerate** (`POST .../test-design/refine` | `regenerate`) still works and posts the same style of delta comment. **Approval stays in QSwarm** (`/approvals/...`). Migrations: **`20260421_0010_test_design_versions_feedback`**, **`20260421_0011_jira_review_issue_and_comment_events`**.  
5. `GET /approvals/{approval_id}` — inspect the pending approval (**approval remains in QSwarm**, not in Jira).  
6. `POST /approvals/{approval_id}/approve` or `.../reject` with `{"actor_id": "reviewer", "notes": "..."}`.  
7. `GET /audit/workflow/{run_id}` — inspect the audit trail.

**Publish layer:** internal artifacts stay the source of truth; a canonical **`TestDesignPublishPackage`** is built from the `test_design` artifact and passed to **`JiraTestDesignPublisher`**, which creates **one** review Task (label `qswarm-draft-test-design-review`), **Relates** link to the parent story, optional assignee via **`JIRA_DEFAULT_TEST_REVIEWER_ACCOUNT_ID`**, and an optional parent summary comment. TestRail/Xray/Zephyr publishers are not built yet.

Other useful endpoints: `GET /health`, `GET /health/db`, `GET /jira/connection-test`, `GET /jira/issues/{issue_key}`, `GET /jira/story/{issue_key}`, `POST /jira/search`, `POST /intake/from-jira/{issue_key}`.

### Jira pickup polling (Sprint 1, manual)

Sprint 1 can still be started explicitly via `POST /workflow/runs` and `POST /workflow/runs/{id}/start`. Additionally, a **label-driven pickup** path finds Jira **Story** or **Task** issues tagged for QSwarm and **creates + starts** a workflow run in one go.

- **Trigger:** Jira label **`qswarm-test-design`** (not a custom workflow status—teams keep their own statuses).
- **How it works:** `POST /jira/pickup/poll` runs a JQL search (`labels = "qswarm-test-design" AND issuetype in (Story, Task)`), applies **preflight** rules per issue, then reuses existing workflow services to create and start runs. There is **no background scheduler** in v1—call the endpoint when you want a poll.
- **Duplicate prevention:** If a **non-terminal** workflow run already exists for the same Jira issue key (`pending`, `running`, `awaiting_approval`, or `approved`), the issue is **skipped** with reason `duplicate_active_run`. Terminal runs (`completed`, `rejected`, `failed`) do not block a new pickup.
- **Optional fields:** Missing description or acceptance criteria in Jira does **not** block pickup; a non-empty summary (that passes a simple “not too vague” check) is enough.
- **Skips:** Ineligible issues are listed in the JSON response with a `reason` code; pickup-related audit events are written when the audit helper is used (`jira_pickup_*` event types).

**Manual test with real Jira**

1. Set live Jira env vars (`JIRA_USE_STUB=false`, base URL, email, token) and restart the API.
2. Create or pick a **Story** or **Task**, set **Summary**, ensure status is **not** in Jira’s **Done** category, and add label **`qswarm-test-design`**.
3. `POST /jira/pickup/poll` with optional body `{"limit": 10}`.
4. Inspect the response (`picked_up`, `skipped`, `results[]`) and `GET /workflow/runs/{id}` for a picked-up run.

**Manual real Jira check:** set `JIRA_USE_STUB=false` and valid `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` in `.env`, restart the API, open `/docs`, call `GET /jira/connection-test` (uses sample issue `NSP-677`), then `GET /jira/issues/NSP-677` for a normalized issue payload (no raw Jira JSON).

## Automation jobs (Sprint 2)

- `POST /automation/jobs` — optional case hints: `case_title`, `case_description`, `preconditions`, `steps`, `expected_results` (stored until start; used to build **`case_spec_json`**). Optional **`repo_owner`** / **`repo_name`** (GitHub `owner/repo` for later **`/create-pr`**; defaults can also come from env).
- `GET /automation/jobs/{job_id}` — includes `framework_summary_json`, **`case_spec_json`**, **`repo_context_json`**, `framework_type`, `change_plan_json`, **`generated_patch_json`**, **`execution_result_json`**, **`failure_analysis_json`**, **`repair_result_json`**, `repo_owner`, `repo_name`, `final_result_json` when set
- `GET /automation/jobs` — newest first
- `POST /automation/jobs/{job_id}/start` — requires valid **`repo_path`** on disk (no automatic clone on this route). Playwright: scan + case spec + bounded repo context → **`planning_changes`**. Unknown: **`failed`** (HTTP **200**). Bad path: **400**.
- `POST /automation/jobs/{job_id}/plan` — only from **`planning_changes`** with all of **`framework_summary_json`**, **`case_spec_json`**, **`repo_context_json`** present. Produces **`change_plan_json`** and **`generating_code`**, or **422** if the plan fails validation (job → **`failed`**). Wrong state: **409**. Missing inputs: **400**.
- `POST /automation/jobs/{job_id}/generate` — only from **`generating_code`** with plan + context + **`repo_path`**. Stub provider returns full-file entries; QSwarm validates scope vs **`change_plan_json`**, writes files under **`repo_path`**, stores **`generated_patch_json`**, then **`executing`**. Validation/apply failure → **`failed`** (**422** with `invalid_generated_patch` or `workspace_apply_failed`). Wrong state: **409**. Missing inputs: **400**.
- `POST /automation/jobs/{job_id}/execute` — only from **`executing`**, Playwright-only. Target file: **`change_plan_json.target_test_file`**, else **`generated_patch_json.target_test_file`**. Wrong state: **409**. Missing prerequisites (e.g. not Playwright summary, missing target, bad **`repo_path`**) → **400** `execution_prerequisites_missing`. HTTP **200** after a completed run attempt (pass → **`awaiting_automation_review`**, fail → **`failed`** with **`execution_result_json`**).
- `POST /automation/jobs/{job_id}/repair` — only when status is **`failed`**, **`execution_result_json`** is present (failed run), and **`repair_result_json`** is still unset. **409** if the job is not **`failed`** or repair was already completed. **400** `repair_prerequisites_missing` if plan/framework/repo prerequisites are missing. **200** when the flow finishes in **`awaiting_automation_review`**, **`awaiting_human_input`**, or **`failed`** (with **`failure_analysis_json`** / **`repair_result_json`** updated as appropriate).
- `POST /automation/jobs/{job_id}/approve` — only from **`awaiting_automation_review`**. Body: **`actor_id`**. Persists an **`approve`** review action; status → **`approved_for_pr`**. Wrong state: **409**.
- `POST /automation/jobs/{job_id}/request-revision` — only from **`awaiting_automation_review`**. Body: **`actor_id`**, **`instruction_text`**. Persists **`request_revision`**, runs revision patch + one re-execution; final status **`awaiting_automation_review`**, **`awaiting_human_input`**, or **`failed`**. **400** if execution prerequisites are missing; wrong state: **409**.
- `POST /automation/jobs/{job_id}/manual-edit-ack` — from **`awaiting_automation_review`** or **`failed`** (with valid Playwright/repo/plan context). Body: **`actor_id`**, **`note`**. Persists **`manual_edit_ack`** and re-runs tests only (no coding provider). **400** / **409** analogous to revision where applicable.
- `POST /automation/jobs/{job_id}/create-pr` — only from **`approved_for_pr`**. Runs a **pre-PR** flow on the **local** repo at **`repo_path`**: verify git, ensure **`qswarm/…`** branch, **fetch** + **merge** latest **`base_branch`** (merge, not rebase). **Merge conflict** → job **`awaiting_human_input`**, a **`pr_records`** row with **`base_refresh_conflict`**, audit **`automation_base_refresh_conflict`** — no commit/PR. If refresh did not change the branch and the last execution was already passing, **Playwright is not re-run**; if the merge updated the branch, **tests run again** before commit. Before **commit**, QSwarm sets **repo-local** `git config user.name` / `user.email` from **`QSWARM_GIT_AUTHOR_NAME`** and **`QSWARM_GIT_AUTHOR_EMAIL`** (required; avoids depending on server global git identity). Then **stage + commit + `git push`** and **GitHub REST** `POST /repos/{owner}/{repo}/pulls` (requires **`GITHUB_TOKEN`** and owner/repo on the job or **`GITHUB_DEFAULT_*`** env). Success → job **`pr_created`** and **`pr_records`** row **`pr_created`** with **PR URL/number**. Other git/GitHub failures → **`failed`**. Response includes **`pr_url`** / **`pr_number`** when created. **Merge remains manual** — QSwarm does not merge PRs. Migration **`20260408_0008_pr_records_and_github_repo_fields`**.

## Configuration

| Variable | Purpose |
|----------|---------|
| `APP_NAME`, `APP_ENV`, `APP_DEBUG` | App metadata / logging |
| `DATABASE_URL` | SQLAlchemy URL (`postgresql+psycopg://...` recommended) |
| `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` | Live Jira (Basic auth) |
| `JIRA_USE_STUB` | When `true` or token missing, Jira calls use stub data |
| `JIRA_DEFAULT_TEST_REVIEWER_ACCOUNT_ID` | Optional Atlassian `accountId` to assign generated draft Jira Tasks after publish |
| `INTERNAL_ACTOR_DEFAULT` | Placeholder default actor (Sprint 1) |
| `CODING_PROVIDER` | Change planning provider (`stub` default; real Codex/Claude later) |
| `QSWARM_CLAUDE_CODE_ENABLED` | When `true`, session `coding_engine=claude_code` may run the CLI adapter (still requires a valid `QSWARM_CLAUDE_CODE_COMMAND` on the server) |
| `QSWARM_CLAUDE_CODE_COMMAND` | Executable for Claude Code–compatible CLI (argv0 only; no shell) |
| `QSWARM_CLAUDE_CODE_EXTRA_ARGS` | Optional argv tokens (`shlex.split`), e.g. `-c pass` with `python` for tests |
| `QSWARM_CLAUDE_CODE_TIMEOUT_SECONDS` | Subprocess timeout for each Claude invocation |
| `QSWARM_CLAUDE_CODE_WORKING_MODE` | Reserved (`one_shot` default); informational |
| `QSWARM_CLAUDE_CODE_ALLOW_REVISION` | When `false`, adapter rejects revision requests |
| `QSWARM_COPILOT_AGENT_ENABLED` | When `true`, session `coding_engine=copilot_agent` may run the Copilot CLI adapter (requires **`QSWARM_COPILOT_AGENT_COMMAND`**) |
| `QSWARM_COPILOT_AGENT_COMMAND` | Copilot CLI executable (argv0) or absolute path (no shell) |
| `QSWARM_COPILOT_AGENT_EXTRA_ARGS` | Optional argv tokens (`shlex.split`) before `-p` + prompt |
| `QSWARM_COPILOT_AGENT_ALLOW_REVISION` | When `false`, adapter rejects revision requests |
| `QSWARM_COPILOT_AGENT_TIMEOUT_SECONDS` | Subprocess timeout for each Copilot CLI invocation |
| `PLAYWRIGHT_EXECUTION_TIMEOUT_SECONDS` | Subprocess timeout for `POST .../execute` (default **120**) |
| `GITHUB_TOKEN` | PAT for hosted **git clone** on session start (GitHub HTTPS), **`POST /automation/jobs/{id}/create-pr`**, and **session** **`POST /automation/sessions/{id}/create-pr`** (repo scope: contents + pull requests). Repository connections may reference a different env var via **`credential_reference`** when **`auth_type=github_pat_env`**. Never commit real tokens. |
| `QSWARM_GIT_AUTHOR_NAME` | Commit author **name** for PR creation (`git config user.name` in the repo only; required with **`QSWARM_GIT_AUTHOR_EMAIL`**) |
| `QSWARM_GIT_AUTHOR_EMAIL` | Commit author **email** for PR creation (`git config user.email` in the repo only; required with **`QSWARM_GIT_AUTHOR_NAME`**) |
| `QSWARM_WORKSPACE_ROOT` | Root for materialized session repos (default **`/tmp/qswarm`**; clones live under **`sessions/<session_id>/repo`**) |
| `QSWARM_WORKSPACE_CACHE_TTL_MINUTES` | Idle TTL for **`workspace_cache_entries`** metadata (default **60**); expired rows are marked before **`create-pr`**; missing managed workspaces are **re-cloned** and the **current patch version** is re-applied automatically |
| `QSWARM_GIT_CLONE_TIMEOUT_SECONDS` | `git clone` timeout for session workspace materialization (default **600**) |
| `QSWARM_GIT_FETCH_TIMEOUT_SECONDS` | `git fetch` / checkout timeouts (default **120**) |
| `QSWARM_BOOTSTRAP_TIMEOUT_SECONDS` | `npm ci` / `npm install` timeout after clone or when deps missing locally (default **600**) |
| `QSWARM_SKIP_BOOTSTRAP_IF_NODE_MODULES` | When **true**, local-existing workspaces skip npm if `node_modules` is non-empty (default **true**) |
| `GITHUB_DEFAULT_REPO_OWNER`, `GITHUB_DEFAULT_REPO_NAME` | Fallback GitHub repo when job has no `repo_owner` / `repo_name` |
| `GITHUB_API_BASE_URL` | GitHub API host (default `https://api.github.com`) |
