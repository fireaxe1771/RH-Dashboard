# Azure Billing Analytics — Execution Plan Summary

I have read all 10 specification documents and examined the existing codebase. Below is what I will execute, precisely as written in `IMPLEMENTATION_PLAN.md`.

## Baseline (verified before changes)
- Backend: `pytest` → **25 passed** (Python 3.11.11 venv, matches Dockerfile)
- Frontend: verifying `npm run test` + `npm run build` baseline now

## Key conventions I will follow (from doc 01)
- Surgical additions only — never rewrite existing files.
- Async-first; Pydantic v2 at all boundaries; all config via `Settings` in `config.py`.
- `str | None` / `list[str]` union syntax (not `Optional`/`List`).
- Every new backend module gets a `backend/tests/test_billing_*.py`; all external calls (Azure SDK, OpenAI, aiohttp) mocked.
- No secrets in code; separate `AZURE_BILLING_*` service principal from `AZURE_CLIENT_ID`.
- Frontend: inline styles, `lucide-react` icons, existing CSS-variable palette.

## One resolved contradiction
Doc 07 §3.2 references Recharts for charts, but **recharts is not a dependency** and doc 01 forbids charting libraries. The existing `WidgetCard.tsx` hand-rolls inline SVG charts. Per the non-negotiable rules, I will hand-roll SVG charts (no new library), consistent with existing code.

## Phases
1. **Dependencies & Config** — add 9 packages to `requirements.txt`; extend `config.py` with billing/AI/sync settings + `validate_billing_settings()`; append `.env.example` blocks.
2. **Billing Auth** — `billing/__init__.py` (custom exceptions), `billing/auth.py` (cached `ClientSecretCredential`), temp `GET /api/billing/auth/test`, tests.
3. **MongoDB Collections & Indexes** — extend `database.py init_indexes()` for 11 collections; create `billing_models.py`.
4. **Core Sync: Cost Details** — `cost_management.py`, `sync_service.py`, `scheduler.py`, lifespan integration, `billing_routes.py` (sync status/trigger), tests.
5. **Supporting APIs** — `advisor.py`, `billing_accounts.py`, `consumption.py`, `resource_graph.py`, `retail_prices.py`; cost/budget/alert/advisor/invoice/reservation routes; tests.
6. **AI Vectorization** — `vectorizer.py` (7 doc types → embeddings → vector search), `POST /api/billing/ai/query`, vector-index startup warning, tests.
7. **Frontend** — `billingApi.ts`; `Sidebar.tsx` collapsible "Azure Billing" section; 8 components in `components/billing/`; `App.tsx` routing; Vitest tests.
8. **Infrastructure** — `terraform/billing_variables.tf`; `main.tf` env/secret blocks; `min_replicas = 1`; `deploy.yml` `TF_VAR_*` injections.
9. **Verification & Hardening** — full `pytest`/`npm test`/`npm build`/`terraform validate`; remove temp auth endpoint; README section.

Each phase's verification gate is run before moving to the next. A PR will be opened and CI driven to green.
