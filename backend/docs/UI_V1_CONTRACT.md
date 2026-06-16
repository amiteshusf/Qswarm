# `/api/v1` — two-tier contract model

QSwarm Web should prefer **one integration model** with two tiers:

1. **Backend-first (default)** — `/api/v1` mirrors the same Pydantic payloads as legacy REST routes, with **only** recursive `camelCase` key conversion (`app/services/ui_v1_mapper.py`) where JSON is returned. No parallel “UI-only” field renames (`owner` vs `ownerOrOrg`, etc.).

2. **Intentional BFF** — Endpoints that **aggregate** or **reshape** multiple sources for a screen keep explicit normalizers (`ui_v1_dashboard.py`, `ui_v1_sessions.py`, `ui_v1_branch_policies.py` today).

Full classification: **`docs/UI_V1_API_CLASSIFICATION.md`**.

## Rules

- **Legacy routes** (`/repo-connections`, `/automation/sessions`, …) stay the canonical **OpenAPI/snake** source; `/api/v1` may expose the same shapes with camelCase keys for browser clients.
- **Do not** add per-CRUD mapper modules unless the endpoint merges multiple backends or requires a stable UI enum not present on the server model.
- **Breaking changes** to BFF view-models should be versioned (e.g. `/api/v2`); backend-first payloads should track `app/schemas` + Alembic.

## Contract enforcement (CI)

- **`@pytest.mark.contract`** — BFF / view-model endpoints (dashboard, sessions, branch policies) where Zod-shaped or multi-source responses remain.
- **Repo connections & settings** — assert parity with `RepositoryConnectionResponse` / settings dict (see `tests/test_ui_v1_repo_connections.py` and settings tests).

## BFF modules (current)

| Area | Module | Role |
|------|--------|------|
| Dashboard | `ui_v1_dashboard.py` | Counts, recent sessions, mixed JSON for UI enums |
| Branch policies | `ui_v1_branch_policies.py` | Policy list/detail shape (until aligned to `BranchPolicyResponse` + generic camel) |
| Sessions | `ui_v1_sessions.py` | List filter + session detail aggregate |
| Generic camelCase | `ui_v1_mapper.py` | `dict_keys_to_camel` for backend-first routes |

## Frontend direction

[Qswarm-UI](https://github.com/amiteshusf/Qswarm-UI) should align Zod/clients to **backend models** for CRUD (e.g. `items` on list repo connections, `ownerOrOrg`, `credentialReference`). Prefer future **OpenAPI-generated** types from this backend over hand-maintained duplicate schemas.
