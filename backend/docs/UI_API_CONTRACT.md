# QSwarm UI API Contract (`/api/v1`)

**Version:** 1.0.0 (2026-07-24)  
**Status:** Authoritative backend source of truth for the separate QSwarm Web frontend.  
**OpenAPI slice:** `docs/openapi-ui-v1.json`  
**Fixtures:** `docs/api-fixtures/*.json`  
**Pydantic models:** `app/schemas/ui_v1_contract.py`, `app/schemas/ui_v1_stories.py`

---

## 1. Purpose and versioning policy

- `/api/v1` is the **only** UI-facing API prefix for QSwarm Web.
- Breaking response changes require a new API version (`/api/v2`) or explicit changelog entry.
- Additive fields are allowed; frontend schemas should use `.passthrough()` or ignore unknown keys unless using strict mode intentionally.
- Legacy routes (`/workflow/*`, `/automation/*`, `/repo-connections`) remain for backward compatibility but new UI work should use `/api/v1`.

## 2. Base path and authentication

| Item | Value |
|------|-------|
| Base URL | `{BACKEND_ORIGIN}/api/v1` |
| Auth | None in current deployment (local/enterprise LAN). Future: bearer token header. |
| Content-Type | `application/json` |
| Request bodies | camelCase preferred; snake_case accepted on models with `populate_by_name` |

## 3. Naming conventions

| Layer | Convention |
|-------|------------|
| JSON keys | **camelCase** on all `/api/v1` responses |
| Query params | camelCase aliases (`projectKey`, `workflowRunId`, `sourceStoryKey`) |
| Internal Python | snake_case |
| Action tokens | snake_case strings in `nextActions` arrays |
| IDs | UUID v4 strings unless noted (Jira keys are uppercase `PROJ-123`) |

## 4. Nullability and optionality rules

| Pattern | Meaning | Zod guidance |
|---------|---------|--------------|
| Key present, value `null` | Field is **nullable** | `z.string().nullable()` |
| Key absent | Rare on `/api/v1` BFF responses; prefer explicit `null` | `z.string().optional()` |
| Empty array `[]` | Valid state (no items) | `z.array(...)` — not optional |
| Empty string `""` | Valid on some timestamp fields when unset | Allow `""` or coerce |

## 5. Date/time conventions

- Format: ISO 8601 strings from Python `datetime.isoformat()`.
- **May omit timezone suffix** on SQLite-backed timestamps (e.g. `"2026-07-24T12:31:07"`).
- UTC-aware timestamps appear on some nested fields (e.g. conversation events with `+00:00`).
- Frontend: parse with `new Date(s)` or use lenient datetime Zod.

## 6. ID conventions

| Entity | Format |
|--------|--------|
| Workflow run | UUID |
| Automation session | UUID |
| Test case record | UUID |
| Agent artifact | UUID |
| Jira story | `PROJECT-NUMBER` uppercase |
| Registry key | `{storyKey}-TC-{nn}` |

## 7. Enum conventions

Enums are **string literals** in JSON. See workflow tables below.

## 8. Error contract

### Wire format (actual)

```json
{
  "detail": {
    "code": "not_found",
    "message": "Workflow run not found",
    "field": null
  }
}
```

| HTTP | Typical `detail.code` | When |
|------|----------------------|------|
| 400 | `invalid_request`, domain codes | Bad input / state |
| 404 | `not_found` | Entity missing |
| 409 | `plan_not_approved`, `active_run_exists`, `stale_version_not_approvable`, … | Conflict / invalid state |
| 422 | FastAPI validation | Malformed body |
| 502 | `jira_error`, provider codes | External integration failure |

**Important:** OpenAPI may reference `ErrorResponse { error: ... }` but handlers emit **`detail`**, not `error`.

Fixture: `docs/api-fixtures/error-not-found.json`

## 9. Sprint 1 workflow stages (`currentStage`)

| `currentStage` | Meaning | `nextActions` | Guaranteed data |
|----------------|---------|---------------|-----------------|
| `intake_ready` | Story ingested, intake artifact stored | `analyze_requirements` | `sourceStory`, `productWorkspace.mode=qswarm_first` |
| `analysis_ready` | Requirement analysis complete | `prepare_plan` | `requirementAnalysis` object |
| `awaiting_plan_approval` | Plan generated, awaiting reviewer | `approve_plan`, `request_plan_revision` | `testDesignPlan` object |
| `plan_revision_requested` | Reviewer requested plan changes | `prepare_plan` | `productWorkspace.planRevisionInstruction` may be set |
| `plan_approved` | Plan approved | `generate_test_cases` | `testDesignPlan.planApproved=true` |
| `awaiting_test_case_review` | Test cases generated, pending approval | `request_revision`, `approve_test_design` | `versions`, `approvalId`, optional `reviewIssue` |
| `approved` | Test design approved | `publish_test_cases` | `status=completed`, `testCaseRecords` may populate |
| `automation_ready` | Published cases in backlog | `view_automation_backlog` | `automationReadyTestCases` |
| `legacy_awaiting_approval` | Pre-workspace LangGraph run at approval | `request_revision`, `approve_test_design` | `productWorkspace` empty, `versions` present |
| `completed` | Terminal success | `view_automation_backlog` | Registry rows |
| `failed` | Terminal failure | `[]` | `blockedReason` may be set |

### Sprint 1 `nextActions` tokens

`analyze_requirements` · `prepare_plan` · `approve_plan` · `request_plan_revision` · `generate_test_cases` · `request_revision` · `approve_test_design` · `publish_test_cases` · `view_automation_backlog`

## 10. Sprint 2 session states

UI dashboard status (`sessionState.status` / list `status`):

`draft` · `plan_ready` · `queued` · `running` · `awaiting_review` · `revising` · `succeeded` · `failed` · `cancelled`

### Sprint 2 `nextActions` tokens (session brief)

`prepare_plan` · `start_automation` · `approve_plan` · `request_plan_revision` · `request_revision` · `approve` · `create_pr` · `open_pr` · `view_summary` · `view_details`

## 11. Endpoint catalog (44 routes)

See `docs/openapi-ui-v1.json` for machine-readable paths. Summary by area:

### A. Story intake

#### `GET /api/v1/stories`

**Response model:** `UiStoryListResponse`  
**Fixture:** `stories-list.json`

```json
{
  "stories": [{ "storyKey": "NSP-696", "title": "...", "readiness": "ready", "hasActiveRun": false, ... }],
  "total": 1
}
```

Query: `projectKey`, `status`, `q`, `limit`

#### `GET /api/v1/stories/{storyKey}`

**Fixture:** `story-detail.json`

#### `POST /api/v1/stories/{storyKey}/test-design-runs`

Body: `{ "initiatedBy": "qswarm-web" }`  
**Response:** Same shape as `GET /test-design-runs/{id}` (run detail).

#### `POST /api/v1/test-design-runs/bulk`

Body: `{ "storyKeys": ["A-1"], "initiatedBy": "..." }`  
Response: `{ "created": [...], "errors": [...] }`

### B. Test-design workspace

#### `GET /api/v1/test-design-runs/{runId}`

**Response model:** `UiTestDesignRunDetail`  
**Fixtures:** `test-design-run-*.json`

```json
{
  "id": "uuid",
  "storyKey": "NSP-696",
  "workflowName": "sprint1_qswarm_workspace",
  "status": "pending",
  "currentStep": "intake_ready",
  "currentStage": "intake_ready",
  "nextActions": ["analyze_requirements"],
  "blockedReason": null,
  "initiatedBy": "qa-lead",
  "createdAt": "2026-07-24T12:31:07",
  "updatedAt": "2026-07-24T12:31:07",
  "sourceStory": { "storyKey": "NSP-696", "intakeArtifactId": "uuid" },
  "requirementAnalysis": null,
  "testDesignPlan": null,
  "versions": [],
  "reviewIssue": null,
  "testCaseRecords": [],
  "automationReadyTestCases": [],
  "approvalId": null,
  "productWorkspace": { "mode": "qswarm_first", "stage": "intake_ready" }
}
```

| Field | Type | Nullable | Notes |
|-------|------|----------|-------|
| `id` | string (UUID) | no | **Primary run identifier** |
| `status` | string | no | DB: `pending\|running\|awaiting_approval\|completed\|failed\|...` |
| `currentStage` | string | no | Product stage (table §9) |
| `nextActions` | string[] | no | May be empty `[]` |
| `requirementAnalysis` | object | yes | `{ version, artifactId, content, createdAt? }` |
| `testDesignPlan` | object | yes | Includes `planApproved`, `planApprovedAt` when set |
| `reviewIssue` | object | yes | `{ reviewJiraIssueKey, publishStatus }` |
| `productWorkspace` | object | no | Internal workspace state; extra keys allowed |

#### `POST .../analyze` → returns analysis object (same as GET `.../analysis`)

#### `GET .../analysis` — fixture `requirement-analysis.json`

#### `POST .../prepare-plan` / `GET .../plan` — fixture `test-design-plan.json`

#### `POST .../approve-plan` / `POST .../request-plan-revision` → returns run detail

#### `POST .../generate-test-cases` → review-data shape + `generation` object

#### `GET .../review-data` — fixture `test-design-review-data.json`

#### `POST .../request-revision` → `{ ok, newVersionNumber, feedbackId, action }`

#### `POST .../approve` → run detail

#### `POST .../publish` → run detail + **`publicationResult`** (extra field)

### C. Test case registry

#### `GET /api/v1/test-cases`

Query: `status` (`automation_ready`), `workflowRunId`, `sourceStoryKey`, `limit`  
Response: `{ "items": [ UiTestCaseRecord, ... ] }`  
**Fixture:** `test-case-list.json`

#### `GET /api/v1/test-cases/{id}` — fixture `test-case-detail.json`

#### `POST /api/v1/test-cases/{id}/publish` → test case detail

#### `POST /api/v1/test-cases/{id}/automate` → **session detail** (Sprint 2 handoff)

### D. Automation sessions

#### `GET /api/v1/sessions` → **top-level array** of session summaries

#### `POST /api/v1/sessions` → session detail — fixture `automation-session-draft.json`

#### `GET /api/v1/sessions/{id}` → session detail (rounds, patches, executions, reviews)

#### `GET /api/v1/sessions/{id}/brief` — fixture `automation-brief.json`

#### `GET /api/v1/sessions/{id}/review-data` → review cockpit (changed files, timeline)

#### Plan/execute lifecycle: `prepare-plan`, `approve-plan`, `request-plan-revision`, `start`, `request-revision`, `approve`, `create-pr`

### E. Platform

| Endpoint | Response |
|----------|----------|
| `GET /dashboard` | Counts + recent sessions — `dashboard.json` |
| `GET /settings` | Read-only config — `settings.json` |
| `GET /repo-connections` | `{ items: [...] }` camelCase |
| `GET /branch-policies` | **top-level array** |
| `POST /branch-policies` | Single policy object |

## 12–20. Additional policies

- **Idempotency:** Registry materialization and publication are idempotent; repeated `publish` does not duplicate Jira issues.
- **Pagination:** `limit` query only; no cursor pagination yet.
- **Traceability:** Jira story → workflow run → artifacts → versions → `testCaseRecords` → `automationSession` (via `testCaseRecordId` FK).
- **Changelog:**
  - **2026-07-24:** `GET /stories` wrapper fixed to `{ stories, total }`.
  - **2026-07-24:** `GET /test-design-runs/{id}` typed with `UiTestDesignRunDetail`; contract fixtures + tests added.

## Frontend developer checklist

1. Read this document and `UI_API_COMPATIBILITY_REPORT.md`.
2. Import fixtures from `docs/api-fixtures/`.
3. Generate or hand-write Zod schemas matching **exact** field names (camelCase).
4. Use `id` for workflow run ID on run detail — not `workflowRunId`.
5. Model errors with `detail.code`, not `error.code`.
6. Run `pytest tests/test_ui_api_contract.py -m contract` when upgrading backend pin.
