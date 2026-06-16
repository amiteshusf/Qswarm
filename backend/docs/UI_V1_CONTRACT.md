# `/api/v1` — stable UI BFF contract (Qswarm Web)

The **QSwarm Web** frontend ([Qswarm-UI](https://github.com/amiteshusf/Qswarm-UI)) treats **`/api/v1`** as the only product-facing HTTP contract. Shapes are defined in the UI repo as Zod schemas (`src/api/schemas.ts`); this backend mirrors them in BFF normalizers under `app/services/ui_v1_*.py`.

## Rules

1. **Legacy routes** (`/automation/sessions`, `/repo-connections`, …) remain for scripts and internal use; they may use snake_case and different response shapes.
2. **`/api/v1`** responses must match the UI Zod contract: correct **wrapper** (many list endpoints return a **top-level JSON array**, not `{ items: … }` or `{ sessions: … }`), **camelCase** field names, and **enum** values normalized for the UI.
3. **Internal models may change** behind explicit mappers; do not return raw SQLAlchemy rows or internal-only dicts from `/api/v1`.
4. **Breaking changes** to this contract should be intentional and versioned (e.g. a future `/api/v2`), not accidental drift.

## Contract enforcement (CI)

- **`tests/test_ui_v1_contract_qswarm_ui.py`** — marked `@pytest.mark.contract`; asserts keys/types/enums for settings, branch policies, sessions against the Qswarm-UI expectations.
- Run all tests: `pytest tests/` (default in CI).
- Run only contract tests: `pytest -m contract`.

If a contract test fails, compare the failing payload to [Qswarm-UI `schemas.ts`](https://github.com/amiteshusf/Qswarm-UI/blob/main/src/api/schemas.ts) and update the appropriate `app/services/ui_v1_*.py` mapper (not the legacy route).

## Endpoint → normalizer map (summary)

| Area | Normalizer module |
|------|-------------------|
| Dashboard | `ui_v1_dashboard.py` |
| Repo connections | `ui_v1_repo_connections.py` |
| Branch policies | `ui_v1_branch_policies.py` |
| Sessions (list/detail/mutations) | `ui_v1_sessions.py` |
| Settings | `ui_v1_settings.py` |
| Generic camelCase (non-dashboard) | `ui_v1_mapper.py` |

## List response pattern (Qswarm-UI client)

The UI client parses **`GET /repo-connections`**, **`GET /branch-policies`**, and **`GET /sessions`** as **`z.array(...)`** — the HTTP body must be a **JSON array** `[...]`, not an object wrapper.
