# Azure Billing Analytics — Implementation Plan

**Project:** RecoveryHub Dashboard System  
**Feature:** Azure Billing API Integration, Cost Analytics, and AI-Powered Cost Optimization  
**Document Version:** 1.0  
**Date:** 2026-06-08  
**Status:** Ready for Implementation

---

## Overview

This document is the authoritative master plan for implementing full Azure billing data ingestion, persistence, and AI-powered analytics into the RecoveryHub Dashboard. It is designed to be handed directly to a Devin Cloud agent for implementation.

The implementation connects to six Azure billing API namespaces, persists all data into MongoDB, vectorizes billing data for semantic AI consumption, and exposes everything through new backend API endpoints and frontend dashboard components.

**Before any code is written, the operator must complete the manual Azure portal setup steps in [Supporting Doc 02 — Azure Auth Setup Guide](./02-azure-auth-setup-guide.md) and populate all required environment variables listed in [Supporting Doc 09 — Environment Variables Reference](./09-environment-variables-reference.md).**

---

## Critical: Application Rules

All code written during this implementation **must comply** with every rule in [Supporting Doc 01 — Application Rules and Conventions](./01-app-rules-and-conventions.md). This document defines the exact coding style, patterns, naming conventions, error handling approach, testing requirements, and architectural constraints that govern every file in this repository. Deviating from these rules will break consistency with the existing codebase and will require rework.

Key rules that most commonly affect new feature work:

1. **Fail loudly at startup.** Missing required environment variables must raise `ValueError` and halt the server — never silently default or ignore.
2. **Async-first.** All MongoDB operations use `motor` (async). All I/O-bound work is `async def`. Sync CPU-bound or blocking work is dispatched via `asyncio.get_event_loop().run_in_executor()`.
3. **Pydantic v2 models for all data.** Every request body, response body, and internal data shape has a Pydantic BaseModel. No raw dicts in API boundaries.
4. **No secrets in code.** All credentials flow through `config.py` → `Settings` class → environment variables. Never hardcode keys, secrets, or IDs.
5. **Tests required.** Every new module must have a corresponding pytest test file under `backend/tests/`. Tests mock all external dependencies (Azure SDK, OpenAI, MongoDB).
6. **No comments added or removed arbitrarily.** Preserve all existing comments. Add comments only where the code is non-obvious.
7. **Python 3.11.** All type annotations use `str | None` union syntax (not `Optional[str]`).

---

## Repository Structure Context

```
E:\gitrepo\RH Dashboard\
├── backend/                    # Python 3.11 FastAPI application
│   ├── main.py                 # App entry point, lifespan, router registration
│   ├── config.py               # Settings class, env var loading, startup validation
│   ├── database.py             # DatabaseManager, Motor client, init_indexes()
│   ├── auth.py                 # Entra ID JWT token verification (user-facing)
│   ├── models.py               # Pydantic v2 models
│   ├── target_db.py            # Azure SQL connection and query execution
│   ├── requirements.txt        # Python dependencies
│   ├── Dockerfile              # Python 3.11-slim-bookworm with ODBC dependencies
│   └── tests/                  # pytest test suite
├── frontend/                   # React 18 + TypeScript + Vite
│   ├── src/
│   │   ├── components/         # React components
│   │   ├── services/api.ts     # Typed API client
│   │   └── ...
│   └── package.json
├── terraform/                  # Azure Container Apps infrastructure (IaC)
├── docker-compose.yml          # Local dev orchestration
├── .env.example                # Environment variable template
└── docs/
    └── azure-billing-analytics-phase-zero-audit/   # THIS directory
        ├── IMPLEMENTATION_PLAN.md                  # This file
        ├── 01-app-rules-and-conventions.md
        ├── 02-azure-auth-setup-guide.md
        ├── 03-azure-billing-api-reference.md
        ├── 04-mongodb-schema-design.md
        ├── 05-backend-module-architecture.md
        ├── 06-ai-vectorization-design.md
        ├── 07-frontend-integration-guide.md
        ├── 08-infrastructure-and-deployment.md
        └── 09-environment-variables-reference.md
```

---

## New File Map

All new files to be created (no existing files deleted):

```
backend/
├── billing/                         # New package
│   ├── __init__.py
│   ├── auth.py                      # ClientSecretCredential for billing APIs
│   ├── cost_management.py           # Microsoft.CostManagement API calls
│   ├── consumption.py               # Microsoft.Consumption API calls
│   ├── advisor.py                   # Microsoft.Advisor API calls
│   ├── billing_accounts.py          # Microsoft.Billing API calls
│   ├── resource_graph.py            # Azure Resource Graph queries
│   ├── retail_prices.py             # Azure Retail Prices public API
│   ├── sync_service.py              # Sync orchestration layer
│   ├── vectorizer.py                # Embedding generation and semantic search
│   └── scheduler.py                 # APScheduler async job definitions
├── billing_models.py                # Pydantic models for billing data
├── billing_routes.py                # FastAPI router for /api/billing/* endpoints
└── tests/
    ├── test_billing_auth.py
    ├── test_billing_cost_management.py
    ├── test_billing_sync_service.py
    ├── test_billing_vectorizer.py
    └── test_billing_routes.py

frontend/src/
├── components/billing/              # New billing UI components
│   ├── BillingOverview.tsx
│   ├── CostTrendChart.tsx
│   ├── TopSpendersTable.tsx
│   ├── BudgetCard.tsx
│   ├── AdvisorPanel.tsx
│   ├── InvoiceList.tsx
│   ├── ReservationDashboard.tsx
│   └── AICostAnalyst.tsx
└── services/billingApi.ts           # Billing API client

terraform/
└── billing_variables.tf             # New Terraform variables for billing secrets
```

---

## Modified Files

The following existing files are modified (never replaced/rewritten from scratch — surgical additions only):

| File | Change |
|---|---|
| `backend/requirements.txt` | Add 9 new packages (see Phase 1) |
| `backend/config.py` | Add billing and AI env vars to Settings class |
| `backend/database.py` | Extend `init_indexes()` with billing collection indexes |
| `backend/main.py` | Import and mount billing router; start/stop scheduler in lifespan |
| `.env.example` | Add billing and AI variable blocks with comments |
| `terraform/variables.tf` | Add billing secret variables |
| `terraform/main.tf` | Add billing secret env vars to backend Container App |
| `.github/workflows/deploy.yml` | Add billing secret TF_VAR injections |
| `frontend/src/components/Sidebar.tsx` | Add Azure Billing navigation section |
| `frontend/src/App.tsx` | Add billing route handling and state |

---

## Phase-by-Phase Implementation Plan

---

### Phase 1 — Dependencies and Configuration

**Objective:** Add all new packages and extend the configuration layer without breaking anything.

**Steps:**

**1.1 — Add packages to `backend/requirements.txt`**

Add the following lines after the existing entries:
```
azure-mgmt-costmanagement>=4.0.0
azure-mgmt-consumption>=10.0.0
azure-mgmt-advisor>=9.0.0
azure-mgmt-billing>=6.0.0
azure-mgmt-resourcegraph>=8.0.0
azure-mgmt-subscription>=3.0.0
apscheduler==3.10.4
openai>=1.30.0
aiohttp>=3.9.0
aiofiles>=23.0.0
```

**1.2 — Extend `backend/config.py` Settings class**

Add the following attributes to the `Settings` class (after the existing Entra ID block):
```python
# --- Azure Billing Integration ---
AZURE_BILLING_CLIENT_ID: str = os.getenv("AZURE_BILLING_CLIENT_ID", "")
AZURE_BILLING_CLIENT_SECRET: str = os.getenv("AZURE_BILLING_CLIENT_SECRET", "")
AZURE_SUBSCRIPTION_ID: str = os.getenv("AZURE_SUBSCRIPTION_ID", "")
AZURE_BILLING_ACCOUNT_ID: str = os.getenv("AZURE_BILLING_ACCOUNT_ID", "")
AZURE_BILLING_ACCOUNT_TYPE: str = os.getenv("AZURE_BILLING_ACCOUNT_TYPE", "MOSP")
AZURE_MANAGEMENT_GROUP_ID: str = os.getenv("AZURE_MANAGEMENT_GROUP_ID", "")

# --- AI / Embeddings ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_CHAT_MODEL: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# --- Billing Sync Configuration ---
BILLING_SYNC_ENABLED: bool = os.getenv("BILLING_SYNC_ENABLED", "true").lower() == "true"
BILLING_DAILY_SYNC_HOUR: int = int(os.getenv("BILLING_DAILY_SYNC_HOUR", "2"))
BILLING_HISTORY_MONTHS: int = int(os.getenv("BILLING_HISTORY_MONTHS", "12"))
```

Add a `validate_billing_settings()` method (called from `validate_settings()` only when `BILLING_SYNC_ENABLED=true` and `TESTING != "true"`):
```python
def validate_billing_settings(self) -> None:
    missing = []
    if not self.AZURE_BILLING_CLIENT_ID:
        missing.append("AZURE_BILLING_CLIENT_ID")
    if not self.AZURE_BILLING_CLIENT_SECRET:
        missing.append("AZURE_BILLING_CLIENT_SECRET")
    if not self.AZURE_SUBSCRIPTION_ID:
        missing.append("AZURE_SUBSCRIPTION_ID")
    if not self.OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise ValueError(f"Missing required billing variables: {', '.join(missing)}")
```

**1.3 — Add billing block to `.env.example`**

Append to `.env.example` after the existing Entra ID block. Follow the exact existing comment style (see Supporting Doc 09 for the full block).

**Verification for Phase 1:**
```bash
cd backend
python -c "from config import settings; print('Config OK')"
pytest -v --tb=short
```
Both commands must succeed with no errors.

---

### Phase 2 — Billing Auth Module

**Objective:** Create the `billing/` package and auth module. Verify token acquisition before writing any sync code.

**Steps:**

**2.1 — Create `backend/billing/__init__.py`**

Empty file.

**2.2 — Create `backend/billing/auth.py`**

See [Supporting Doc 05 — Backend Module Architecture](./05-backend-module-architecture.md), Section 2.1 for the full specification.

Key design:
- Uses `azure.identity.ClientSecretCredential` with `settings.AZURE_BILLING_CLIENT_ID`, `settings.AZURE_BILLING_CLIENT_SECRET`, `settings.AZURE_TENANT_ID`
- Exposes `get_billing_token() -> str` — returns a raw bearer token string
- Exposes `get_billing_credential() -> ClientSecretCredential` — returns the credential object for SDK clients
- Caches the credential as a module-level singleton (instantiated once on first call)
- Token scope: `https://management.azure.com/.default`
- The credential object handles automatic token refresh internally

**2.3 — Add auth test endpoint to `billing_routes.py` (temporary)**

`GET /api/billing/auth/test` — protected by `get_current_user`, calls `get_billing_token()`, returns `{"status": "ok", "token_prefix": token[:10]}`. This is a temporary validation endpoint, removed after Phase 2 is verified.

**2.4 — Create `backend/tests/test_billing_auth.py`**

Test that `get_billing_credential()` returns a `ClientSecretCredential` instance with mocked settings. Test that `get_billing_token()` calls `credential.get_token()` and returns the token string.

**Verification for Phase 2:**
- All existing tests still pass: `pytest -v`
- The test endpoint returns `200 OK` when called with a valid user token and valid billing credentials

---

### Phase 3 — MongoDB Billing Collections and Indexes

**Objective:** Define all MongoDB collections and indexes before writing any sync code.

**Steps:**

**3.1 — Extend `backend/database.py` `init_indexes()`**

Add index creation for all 11 billing collections. See [Supporting Doc 04 — MongoDB Schema Design](./04-mongodb-schema-design.md) for exact index specifications.

Collections to index:
- `azure_cost_details`
- `azure_cost_summary`
- `azure_invoices`
- `azure_budgets`
- `azure_advisor_recommendations`
- `azure_reservation_details`
- `azure_reservation_recommendations`
- `azure_resource_inventory`
- `azure_retail_prices`
- `azure_billing_sync_log`
- `azure_billing_vectors`

**3.2 — Create `backend/billing_models.py`**

Pydantic v2 models for all billing data shapes. See [Supporting Doc 05](./05-backend-module-architecture.md), Section 1 for full model definitions. Every collection document has a corresponding Pydantic model.

**Verification for Phase 3:**
```bash
pytest -v
```
No failures. Verify new indexes are created by starting the app locally and checking MongoDB collections.

---

### Phase 4 — Core Sync: Cost Details

**Objective:** Implement the primary data pipeline for cost and usage data.

**Steps:**

**4.1 — Create `backend/billing/cost_management.py`**

See [Supporting Doc 03 — Azure Billing API Reference](./03-azure-billing-api-reference.md) and [Supporting Doc 05](./05-backend-module-architecture.md) for full specifications.

Functions to implement:
- `query_costs(scope, start_date, end_date, granularity, group_by_dimensions)` — POST to Query API
- `generate_cost_details_report(scope, start_date, end_date, metric)` — Async: POST → poll → download CSV
- `get_forecast(scope, start_date, end_date)` — POST to Forecast API
- `get_budgets(scope)` — GET budgets list
- `get_alerts(scope)` — GET alerts list

All functions must:
- Use `aiohttp.ClientSession` with the bearer token from `billing/auth.py`
- Implement exponential backoff retry on HTTP 429 and 503 (2s, 4s, 8s, max 60s)
- Log at INFO level for each API call and at WARNING for retries
- Raise `BillingAPIError` (custom exception defined in `billing/__init__.py`) on unrecoverable errors

**4.2 — Create `backend/billing/sync_service.py` (cost functions only)**

Implement `sync_cost_details(billing_period: str)` and `run_full_backfill(months: int)`.

Key behavior:
- `billing_period` format: `"YYYY-MM"` (e.g., `"2026-05"`)
- Calls `generate_cost_details_report()`, parses returned CSV
- Upserts each record to `azure_cost_details` using `update_one(..., upsert=True)` with unique key: `{subscription_id, date, resource_id, meter_id}`
- After cost detail upsert, runs `_rebuild_cost_summary(billing_period)` to regenerate aggregated summaries in `azure_cost_summary`
- Writes a sync log entry to `azure_billing_sync_log` (status: running → completed/failed)
- `run_full_backfill()` checks if `azure_cost_details` is empty before running; if not empty, skips backfill (idempotent)

**4.3 — Create `backend/billing/scheduler.py`**

Uses `apscheduler.schedulers.asyncio.AsyncIOScheduler`. See [Supporting Doc 05](./05-backend-module-architecture.md) Section 2.9 for job definitions and cron schedules.

**4.4 — Integrate scheduler into `backend/main.py` lifespan**

In the existing `lifespan()` async context manager, after the existing startup tasks:
```python
from billing.scheduler import billing_scheduler
if settings.BILLING_SYNC_ENABLED:
    billing_scheduler.start()
    asyncio.create_task(_run_backfill_if_needed())
```
And in the `yield` cleanup block:
```python
if settings.BILLING_SYNC_ENABLED:
    billing_scheduler.shutdown(wait=False)
```

**4.5 — Create `backend/billing_routes.py` (sync/status endpoints only for now)**

Add `GET /api/billing/sync/status` and `POST /api/billing/sync/trigger` endpoints.

**4.6 — Mount billing router in `backend/main.py`**

```python
from billing_routes import billing_router
app.include_router(billing_router, prefix="/api/billing")
```

**4.7 — Write tests: `backend/tests/test_billing_cost_management.py`**

Mock `aiohttp.ClientSession` and Azure SDK responses. Test:
- Successful CSV download and parse
- Retry behavior on 429
- Upsert idempotency (call sync twice, verify record count is the same)

**Verification for Phase 4:**
```bash
pytest -v
```
Trigger a manual sync via `POST /api/billing/sync/trigger` with body `{"sync_type": "full"}` and verify `azure_billing_sync_log` shows a completed entry.

---

### Phase 5 — Supporting API Integrations

**Objective:** Implement the remaining five billing API integrations.

Each step follows the same pattern as Phase 4: implement the API module, add sync functions to `sync_service.py`, and add routes to `billing_routes.py`.

**5.1 — `backend/billing/advisor.py`**

Implement `get_recommendations(subscription_id, category)`. See [Supporting Doc 03](./03-azure-billing-api-reference.md) Section 2.3.
Add `sync_advisor_recommendations()` to `sync_service.py`.
Add routes: `GET /api/billing/advisor/recommendations`, `GET /api/billing/advisor/cost-savings`, `GET /api/billing/advisor/summary`.

**5.2 — `backend/billing/billing_accounts.py`**

Implement `get_billing_accounts()`, `get_billing_periods()`, `get_invoices()`, `get_invoice_transactions()`. See [Supporting Doc 03](./03-azure-billing-api-reference.md) Section 2.4.
Add `sync_invoices()` to `sync_service.py`.
Add routes: `GET /api/billing/invoices`, `GET /api/billing/invoices/{invoice_id}`.

**5.3 — `backend/billing/consumption.py`**

Implement `get_reservation_details()`, `get_reservation_summaries()`, `get_reservation_recommendations()`, `get_marketplace_charges()`. See [Supporting Doc 03](./03-azure-billing-api-reference.md) Section 2.2.
Add `sync_reservations()` to `sync_service.py`.
Add routes: `GET /api/billing/reservations/details`, `GET /api/billing/reservations/recommendations`.

**5.4 — `backend/billing/resource_graph.py`**

Implement `query_resources(subscription_ids, kql_query)` using aiohttp POST. See [Supporting Doc 03](./03-azure-billing-api-reference.md) Section 2.5.
Add `sync_resource_inventory()` to `sync_service.py`.
No API endpoints needed for inventory (used internally for AI context).

**5.5 — `backend/billing/retail_prices.py`**

Implement `get_retail_prices(service_families, arm_region)` using aiohttp against the public `https://prices.azure.com/api/retail/prices` endpoint (no auth). See [Supporting Doc 03](./03-azure-billing-api-reference.md) Section 2.6.
Add `sync_retail_prices()` to `sync_service.py`.

**5.6 — Add remaining cost routes to `billing_routes.py`**

`GET /api/billing/cost/summary`, `GET /api/billing/cost/trend`, `GET /api/billing/cost/top-spenders`, `GET /api/billing/cost/by-tag`, `GET /api/billing/cost/daily`, `GET /api/billing/cost/forecast`, `GET /api/billing/budgets`, `GET /api/billing/alerts`.

All routes read from MongoDB — they do not call Azure APIs directly. Queries use the existing `get_db()` dependency.

**5.7 — Write tests for all new modules**

Each module gets a test file. All external HTTP calls are mocked with `unittest.mock.AsyncMock` or `pytest-mock`.

**Verification for Phase 5:**
```bash
pytest -v
```
All 5 sync types should be triggerable via `POST /api/billing/sync/trigger`. Verify MongoDB collections are populated.

---

### Phase 6 — AI Vectorization Layer

**Objective:** Implement the semantic embedding pipeline and the AI query endpoint.

**Steps:**

**6.1 — Create `backend/billing/vectorizer.py`**

See [Supporting Doc 06 — AI Vectorization Design](./06-ai-vectorization-design.md) for full specification.

Key functions:
- `generate_billing_documents(billing_period)` — queries MongoDB billing collections and produces a list of `BillingVectorDocument` dicts (with `text`, `document_type`, `metadata`)
- `embed_documents(documents)` — calls OpenAI `text-embedding-3-small` in batches of 100, returns documents with `embedding` field populated
- `upsert_vectors(documents)` — upserts to `azure_billing_vectors` using `update_one(upsert=True)` keyed on `{document_type, metadata.period, metadata.dimension_value}`
- `semantic_search(query_text, document_types, top_k)` — embeds query text, runs MongoDB `$vectorSearch` aggregation, returns top-k documents

**6.2 — Add AI query endpoint to `billing_routes.py`**

`POST /api/billing/ai/query`

Request body: `{ "question": str, "document_types": list[str] | None, "period_filter": str | None, "top_k": int = 10 }`

Processing:
1. Embed the question via `semantic_search()`
2. Collect retrieved document texts as LLM context
3. Call `openai.chat.completions.create()` with `settings.OPENAI_CHAT_MODEL`
4. Return `{ "answer": str, "sources": list[dict], "model": str }`

See [Supporting Doc 06](./06-ai-vectorization-design.md) for the system prompt template.

**6.3 — Add vectorization to scheduler**

Schedule `generate_billing_documents` + `embed_documents` + `upsert_vectors` to run after every successful daily sync and after full backfill completes.

**6.4 — MongoDB Atlas Vector Search Index**

The `$vectorSearch` operator requires a vector index that **cannot** be created via the Motor driver's `create_index()`. It must be created either:
- Manually in MongoDB Atlas UI (Database > Collections > Search Indexes > Create Search Index → JSON editor)
- Or via the Atlas Admin API

Add a startup check in `init_indexes()`: query for the existence of the vector index and log a clear `WARNING` if it is missing, with the exact JSON definition the operator needs to create it. Do not raise an error — the rest of the app functions without the vector index.

The required index JSON definition is in [Supporting Doc 04](./04-mongodb-schema-design.md) Section 12.

**6.5 — Write tests: `backend/tests/test_billing_vectorizer.py`**

Mock OpenAI client and MongoDB. Test document generation logic, batch embedding, upsert idempotency, and semantic search pipeline.

**Verification for Phase 6:**
```bash
pytest -v
```
Call `POST /api/billing/ai/query` with `{"question": "What are my top spending services?"}` and verify a coherent response.

---

### Phase 7 — Frontend Integration

**Objective:** Add billing dashboard pages and the AI Cost Analyst interface to the React frontend.

**Steps:**

**7.1 — Create `frontend/src/services/billingApi.ts`**

Typed API client for all `/api/billing/*` endpoints. Follow the exact same patterns as the existing `frontend/src/services/api.ts`. See [Supporting Doc 07 — Frontend Integration Guide](./07-frontend-integration-guide.md) for TypeScript interface definitions.

**7.2 — Update `frontend/src/components/Sidebar.tsx`**

Add a collapsible "Azure Billing" section beneath the existing dashboards list. Navigation items:
- Cost Overview
- Top Spenders
- Budgets & Alerts
- Advisor Recommendations
- Invoices
- Reservations
- AI Cost Analyst

**7.3 — Create billing components**

Create each component in `frontend/src/components/billing/`. Each component follows the existing component conventions: TypeScript functional components, React hooks (`useState`, `useEffect`), `lucide-react` for icons, inline style objects (no separate CSS files), consistent error/loading states.

Components:
- `BillingOverview.tsx` — KPI cards (MTD spend, MoM change, budget utilization, top service)
- `CostTrendChart.tsx` — 12-month area chart using existing Recharts (already a dependency via WidgetCard)
- `TopSpendersTable.tsx` — Sortable table with trend arrows
- `BudgetCard.tsx` — Progress bar with threshold indicators
- `AdvisorPanel.tsx` — Recommendation cards grouped by impact level
- `InvoiceList.tsx` — Invoice history with status badges
- `ReservationDashboard.tsx` — Utilization metrics and purchase opportunity cards
- `AICostAnalyst.tsx` — Chat-style interface with message history

See [Supporting Doc 07](./07-frontend-integration-guide.md) for full component specifications, including prop types, data shapes, and UI patterns.

**7.4 — Update `frontend/src/App.tsx`**

Add billing view state handling. When a billing navigation item is selected, render the corresponding billing component in the main content area instead of the existing dashboard viewer.

**Verification for Phase 7:**
```bash
cd frontend && npm run test
cd frontend && npm run build
```
Both must succeed with no errors or type-check failures.

---

### Phase 8 — Infrastructure Updates

**Objective:** Extend Terraform and CI/CD to support all new billing secrets in production deployment.

**Steps:**

**8.1 — Create `terraform/billing_variables.tf`**

Add Terraform variable declarations for all new billing secrets. See [Supporting Doc 08 — Infrastructure and Deployment](./08-infrastructure-and-deployment.md) for the full variable block.

**8.2 — Update `terraform/main.tf`**

Add `env` blocks to the `backend` Container App template for all billing environment variables. Sensitive values use `secret_name` references. Non-sensitive values use direct `value`. See [Supporting Doc 08](./08-infrastructure-and-deployment.md) for the exact block.

Add `secret` blocks to the Container App resource for all sensitive billing values.

**8.3 — Update `.github/workflows/deploy.yml`**

Add `TF_VAR_*` environment variable injections for all billing GitHub Actions secrets. See [Supporting Doc 08](./08-infrastructure-and-deployment.md) for the exact additions.

**8.4 — Add GitHub Actions secrets**

The following secrets must be added to the GitHub repository (Settings → Secrets and variables → Actions):

| Secret Name | Value |
|---|---|
| `AZURE_BILLING_CLIENT_ID` | From App Registration |
| `AZURE_BILLING_CLIENT_SECRET` | From App Registration client secret |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_BILLING_ACCOUNT_ID` | Billing account ID |
| `AZURE_BILLING_ACCOUNT_TYPE` | `EA`, `MCA`, or `MOSP` |
| `OPENAI_API_KEY` | OpenAI platform API key |

**Verification for Phase 8:**
- Run `terraform plan` locally to verify no errors in modified Terraform files
- Confirm GitHub Actions workflow YAML is valid YAML (no syntax errors)

---

### Phase 9 — End-to-End Verification and Hardening

**Objective:** Full integration test, performance hardening, and documentation update.

**Steps:**

**9.1 — Full test suite**
```bash
cd backend && pytest -v --tb=short
cd frontend && npm run test
```
All tests must pass.

**9.2 — Local integration test**
```bash
docker-compose up --build
```
- Verify app boots without errors
- Trigger `POST /api/billing/sync/trigger` with `{"sync_type": "daily"}`
- Verify sync log shows completion
- Verify `azure_cost_details` and `azure_advisor_recommendations` are populated
- Call `GET /api/billing/advisor/cost-savings` and verify response
- Call `POST /api/billing/ai/query` and verify AI response

**9.3 — Rate limit hardening**

Verify all Azure API calls implement:
- HTTP 429 detection with `Retry-After` header parsing
- Exponential backoff: `2 ** attempt` seconds up to 60s max
- Max 5 retry attempts before raising `BillingAPIError`

**9.4 — Update `README.md`**

Add a "Azure Billing Integration" section with:
- Prerequisites (Azure app registration, billing roles)
- Required environment variables
- How to trigger a manual sync
- How to use the AI Cost Analyst

**9.5 — Remove temporary auth test endpoint**

Remove `GET /api/billing/auth/test` that was added in Phase 2.

---

## Acceptance Criteria

The implementation is complete when all of the following are true:

- [ ] `pytest -v` passes with no failures or warnings
- [ ] `npm run test` passes with no failures
- [ ] `npm run build` produces a clean production build
- [ ] `docker-compose up --build` starts all services without errors
- [ ] All 11 MongoDB billing collections exist with correct indexes
- [ ] A full sync can be triggered and completes successfully
- [ ] All 20+ `/api/billing/*` endpoints return correct data
- [ ] The AI query endpoint returns coherent, sourced cost analysis responses
- [ ] All new billing components render without console errors
- [ ] Terraform files pass `terraform validate`
- [ ] No secrets or credentials appear in any code file
- [ ] All new Python files have corresponding test files

---

## Supporting Documents Index

| Document | Contents |
|---|---|
| [01 — App Rules and Conventions](./01-app-rules-and-conventions.md) | Coding style, patterns, naming, error handling, testing — must be read first |
| [02 — Azure Auth Setup Guide](./02-azure-auth-setup-guide.md) | Step-by-step Azure portal instructions for creating the service principal and assigning roles |
| [03 — Azure Billing API Reference](./03-azure-billing-api-reference.md) | All six API namespaces with endpoints, scopes, request/response shapes, rate limits |
| [04 — MongoDB Schema Design](./04-mongodb-schema-design.md) | All 11 collection schemas, field definitions, index specifications, Atlas Vector Search index JSON |
| [05 — Backend Module Architecture](./05-backend-module-architecture.md) | Function signatures, module responsibilities, error handling, scheduler jobs |
| [06 — AI Vectorization Design](./06-ai-vectorization-design.md) | Document types, text generation templates, embedding pipeline, LLM prompts |
| [07 — Frontend Integration Guide](./07-frontend-integration-guide.md) | TypeScript interfaces, component specs, API client patterns, UI conventions |
| [08 — Infrastructure and Deployment](./08-infrastructure-and-deployment.md) | Terraform additions, GitHub Actions secrets, Container Apps secrets config |
| [09 — Environment Variables Reference](./09-environment-variables-reference.md) | All env vars (old and new), descriptions, example values, `.env.example` blocks |
