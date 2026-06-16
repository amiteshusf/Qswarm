# `/api/v1` endpoint classification — backend-first vs BFF

**Goal:** Backend Pydantic/OpenAPI shapes are the default contract for CRUD. BFF stays only where aggregation or UI-specific semantics are unavoidable until the frontend adopts backend field names.

**Source of truth for “backend contract”:** legacy routers under `/repo-connections`, `/automation/sessions`, etc., plus `app/schemas/*.py`.

| Path | Current role | Backend-first candidate? | Remain BFF (for now)? | Reason |
|------|----------------|---------------------------|------------------------|--------|
| `GET /api/v1/dashboard` | Aggregated counts + recent rows + env | **No** | **Yes** | Multiple queries, `sessionCounts` record, status mapping — true view-model. |
| `GET/POST/PATCH /api/v1/repo-connections` (+ `GET …/{id}`) | CRUD on `RepositoryConnection` | **Yes** | **No** (target) | Single resource; parity with `GET/POST/PATCH /repo-connections` after camelCase pass-through. |
| `GET/POST/PATCH /api/v1/branch-policies` (+ `GET …/{id}`) | CRUD + list by policy id | **Yes** (long term) | **Yes** (short term) | Legacy create is nested under `/repo-connections/{id}/branch-policy`; `/api/v1` exposes a flatter surface. Moving to backend-first means either aligning legacy routes or accepting `BranchPolicyResponse` snake + wrapper in the UI. |
| `GET /api/v1/sessions` | Filtered list | **Yes** (long term) | **Yes** (short term) | Today: UI status enum + camelCase summary. Backend list is `AutomationSessionSummaryResponse` + internal `status` strings. |
| `POST /api/v1/sessions` | Create | **Yes** (long term) | **Partial** | Body still uses `UiAutomationSessionCreate` for Qswarm-UI vs legacy field names; response can move to `AutomationSessionSummaryResponse` + camel when UI catches up. |
| `GET /api/v1/sessions/{id}` | Detail | **No** (as aggregate) | **Yes** | Merges rounds, patches, executions, reviews, inferred `branchPolicyId`, PR preview — multiple sources. |
| `POST …/start`, `…/request-revision`, `…/approve`, `…/create-pr` | Mutations | **Yes** (long term) | **Yes** (short term) | Currently returns full session detail view-model for one round-trip in the UI; legacy returns small DTOs. |
| `GET /api/v1/settings` | Read-only config slice | **Yes** | **No** (target) | Single `Settings` object; expose camelCase projection of real fields (no invented nested `settingsSchema` unless product owns it). |

## Phased simplification (this repo)

- **Done / pilot:** Repo connections and settings on `/api/v1` delegate to the same service layer as legacy routes and use **only** `dict_keys_to_camel(model_dump())` — no dedicated `ui_v1_repo_connections` / `ui_v1_settings` formatters.
- **Remain BFF (documented):** Dashboard; branch policies; sessions list/detail/mutations — reduce in a follow-up once [Qswarm-UI](https://github.com/amiteshusf/Qswarm-UI) schemas and `client.ts` target backend shapes (`items` wrappers, `owner_or_org` / `credential_reference`, etc.) or OpenAPI-generated types.

## Contract tests

- **`@pytest.mark.contract`:** Keep for **BFF** endpoints (dashboard, sessions, branch policies) where a deliberate UI view-model still applies.
- **Backend parity:** Assert `/api/v1/repo-connections` matches camelCase legacy DTO shape (`items`, field names from `RepositoryConnectionResponse`).

## Frontend follow-up (outside this repo)

Point Qswarm Web at:

- `GET /api/v1/repo-connections` → body `{ "items": [ … ] }` with camelCase keys aligned to backend models (e.g. `ownerOrOrg`, `credentialReference`).
- Adjust Zod/client accordingly; remove duplicate “UI-only” repo schema if redundant.
