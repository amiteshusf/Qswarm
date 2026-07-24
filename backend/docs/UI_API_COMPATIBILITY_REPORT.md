# UI API Compatibility Report

**Generated:** 2026-07-24  
**Scope:** All `/api/v1/*` routes consumed by QSwarm Web  
**Policy:** Backend documents and stabilizes the actual contract; frontend updates Zod schemas to match.

## Executive summary

| Metric | Count |
|--------|------:|
| Total `/api/v1` routes | 44 |
| Typed `response_model` | 2 (`GET /stories`, `GET /test-design-runs/{id}`) |
| Raw dict / BFF responses | 42 |
| Documented JSON fixtures | 20 |
| Contract tests | 20 |

### Known frontend mismatch root causes

| Issue | Endpoint | Backend actual | Common frontend mistake |
|-------|----------|----------------|-------------------------|
| List wrapper | `GET /stories` | `{ stories, total }` | `{ items }` — **fixed backend 2026-07-24** |
| Run ID field | `GET /test-design-runs/{id}` | `id` (UUID string) | `workflowRunId`, `runId` |
| Dual status fields | run detail | `status` (DB) + `currentStage` (product) | Using only one |
| Nullable nests | run detail | `requirementAnalysis`, `testDesignPlan`, `reviewIssue` may be `null` | `.optional()` vs `.nullable()` confusion |
| Internal state leak | run detail | `productWorkspace` object always present | Not in early frontend schemas |
| Action tokens | `nextActions` | snake_case e.g. `analyze_requirements` | camelCase e.g. `analyzeRequirements` |
| Error wrapper | all errors | `{ detail: { code, message, field? } }` | `{ error: { code } }` per `ErrorResponse` model |
| Test case list | `GET /test-cases` | `{ items: [...] }` | `{ testCases }` |
| Session list | `GET /sessions` | top-level **array** | `{ items }` wrapper |
| Branch policies list | `GET /branch-policies` | top-level **array** | `{ items }` wrapper |
| Timestamps | many endpoints | ISO8601, often **without** `Z` suffix | strict `datetime()` requiring timezone |

---

## Endpoint classification table

| Endpoint | Typed? | Stability | Frontend risk | Recommended frontend action | Backend change? |
|----------|--------|-----------|---------------|----------------------------|-----------------|
| `GET /api/v1/stories` | Yes | stable | Low | Use `UiStoryListResponse` from OpenAPI | No |
| `GET /api/v1/stories/{key}` | No | stable but undocumented | Medium | Align Zod to `story-detail.json` fixture | Doc only |
| `POST /api/v1/stories/{key}/test-design-runs` | No | stable | Medium | Same shape as run detail (`id` field) | Typed model optional |
| `GET /api/v1/test-design-runs/{id}` | Yes | stable | **High** (active Zod failures) | Use `UiTestDesignRunDetail` + fixtures | **Typed 2026-07-24** |
| `GET /api/v1/test-design-runs/{id}/review-data` | No | stable | Medium | Use `test-design-review-data.json` | Add typing later |
| `GET /api/v1/test-design-runs/{id}/analysis` | No | stable | Low | Artifact ref + content | Doc only |
| `GET /api/v1/test-design-runs/{id}/plan` | No | stable | Low | Plan artifact ref + content | Doc only |
| `POST .../approve-plan` etc. | No | stable | Medium | Returns run detail shape | Same as GET run |
| `POST .../publish` | No | stable | Medium | Run detail + `publicationResult` | Document extra field |
| `GET /api/v1/test-cases` | No | stable | Medium | `{ items }` wrapper | Consider alias later |
| `GET /api/v1/sessions` | No | stable | Medium | Top-level array | Document clearly |
| `GET /api/v1/sessions/{id}` | No | stable | Low | `sessionDetailSchema` — existing contract tests | No |
| `GET /api/v1/sessions/{id}/brief` | No | stable | Medium | `automation-brief.json` | Doc only |
| `GET /api/v1/sessions/{id}/review-data` | No | stable | Medium | Needs fixture | Add fixture |
| `GET /api/v1/dashboard` | No | BFF | Low | `dashboard.json` | No |
| `GET /api/v1/settings` | No | stable | Low | `settings.json` | No |
| `GET /api/v1/repo-connections` | No | backend-first | Low | `{ items }` camelCase | No |
| `GET /api/v1/branch-policies` | No | BFF | Medium | Top-level array | No |
| Legacy `/workflow/*` | N/A | legacy | Low | Do not use from new UI | No |

---

## `GET /test-design-runs/{id}` — mismatch analysis

**Actual backend guarantees** (see `docs/api-fixtures/test-design-run-*.json`):

- Top-level object (no wrapper)
- Primary key: **`id`** (not `workflowRunId`)
- Product navigation: **`currentStage`** + **`nextActions`**
- Persistence status: **`status`** (workflow run DB enum)
- Nullable: `requirementAnalysis`, `testDesignPlan`, `reviewIssue`, `blockedReason`, `approvalId`
- Always present (may be empty): `versions[]`, `testCaseRecords[]`, `automationReadyTestCases[]`, `productWorkspace`
- `sourceStory` is a **minimal ref** `{ storyKey, intakeArtifactId? }` — not full Jira story

**Likely Zod failure causes:**

1. Expecting `workflowRunId` instead of `id`
2. Marking nullable fields as required objects
3. Missing `productWorkspace` in schema (`extra` strict mode)
4. Wrong `nextActions` enum values
5. Confusing `status` with `currentStage`

**Backend change made:** Added `UiTestDesignRunDetail` Pydantic model + `response_model` on GET route. Shape unchanged.

---

## Error contract (actual)

FastAPI returns:

```json
{
  "detail": {
    "code": "not_found",
    "message": "Workflow run not found",
    "field": null
  }
}
```

**Note:** `app/schemas/common.py` defines `ErrorResponse { error: ErrorDetail }` for OpenAPI documentation, but handlers pass `detail=ErrorDetail(...).model_dump()` — the **wire format uses `detail`**, not `error`. Frontend must validate `detail`.

---

## Fixture index

| File | Endpoint / state |
|------|------------------|
| `stories-list.json` | `GET /stories` |
| `story-detail.json` | `GET /stories/{key}` |
| `test-design-run-intake-ready.json` | Run at `intake_ready` |
| `test-design-run-analysis-ready.json` | Run at `analysis_ready` |
| `test-design-run-awaiting-plan-approval.json` | Run at `awaiting_plan_approval` |
| `test-design-run-plan-approved.json` | Run at `plan_approved` |
| `test-design-run-awaiting-test-case-review.json` | Run at `awaiting_test_case_review` |
| `test-design-run-approved.json` | Run at `approved` |
| `test-design-run-automation-ready.json` | Run at `automation_ready` |
| `test-design-run-legacy.json` | Legacy LangGraph run |
| `requirement-analysis.json` | `GET .../analysis` |
| `test-design-plan.json` | `GET .../plan` |
| `test-design-review-data.json` | `GET .../review-data` |
| `test-case-list.json` | `GET /test-cases` |
| `test-case-detail.json` | `GET /test-cases/{id}` |
| `automation-session-draft.json` | `POST /sessions` |
| `automation-brief.json` | `GET /sessions/{id}/brief` |
| `dashboard.json` | `GET /dashboard` |
| `settings.json` | `GET /settings` |
| `error-not-found.json` | 404 shape |

---

## Frontend integration instructions

1. Copy `docs/api-fixtures/*.json` into the frontend repo as contract fixtures.
2. Import `docs/openapi-ui-v1.json` for OpenAPI codegen **or** hand-write Zod from `docs/UI_API_CONTRACT.md`.
3. Use `app/schemas/ui_v1_contract.py` as the Python source of truth for run detail and stories list.
4. Run backend contract tests before releases: `pytest -m contract`.
5. Treat `nextActions` values as **opaque snake_case tokens** defined by backend tables in `UI_API_CONTRACT.md`.
6. Use `.nullable()` in Zod for fields documented as nullable; distinguish from `.optional()` (missing key — backend usually sends key with `null`).
