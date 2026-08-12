# RecoveryHub Dashboard — Project Notes

## Deferred: Azure Billing Configuration

**Status:** On hold as of 2026-07-17. All Azure billing setup work is deferred
until the Azure subscription and billing account are straightened out.

The billing UI, backend sync service, API routes, tests, and Terraform are
all implemented and passing. What remains is **Azure-side configuration only**
— no code changes are needed to enable billing.

### Deferred items

1. **Billing service principal** — create a dedicated App Registration in
   Azure AD with a client secret and assign it the Cost Management Reader
   role on the target subscription or billing account. Fill in:
   - `AZURE_BILLING_CLIENT_ID`
   - `AZURE_BILLING_CLIENT_SECRET`

2. **Billing account identifiers** — determine the subscription ID, billing
   account ID, and account type from the Azure portal. Fill in:
   - `AZURE_SUBSCRIPTION_ID`
   - `AZURE_BILLING_ACCOUNT_ID`
   - `AZURE_BILLING_ACCOUNT_TYPE` (MOSP, MCA, or EA)

3. **Management group scope (optional)** — `AZURE_MANAGEMENT_GROUP_ID` is
   defined in config but not wired into the sync service. If multi-subscription
   scope is needed later, update `_scope()` in `backend/billing/sync_service.py`
   to return `/providers/Microsoft.Management/managementGroups/{id}` when the
   value is set.

4. **AI provider** — configure either Azure OpenAI (Foundry) or OpenAI.com:
   - Azure OpenAI: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and set
     `OPENAI_CHAT_MODEL`/`OPENAI_EMBEDDING_MODEL` to deployment names
   - OpenAI.com: `OPENAI_API_KEY`

5. **Enable billing sync** — set `BILLING_SYNC_ENABLED=true` in `.env` once
   all credentials are in place.

### Goal when resumed

The objective is to analyze:
- What is actually costing us money
- What costs are going up
- Why they are going up

The billing dashboard views (Cost Overview, Top Spenders, Budgets & Alerts,
Advisor, Invoices, Reservations, AI Cost Analyst) are all built and ready to
display data once the sync service is enabled and has populated MongoDB.

## Build & Run

Local Docker Desktop development:
```powershell
.\dev-start.ps1              # Build + run (default)
.\dev-start.ps1 -Build       # Rebuild images + restart
.\dev-start.ps1 -NoCache     # Full clean rebuild + restart
.\dev-start.ps1 -Restart     # Just restart existing containers
.\dev-start.ps1 -Stop        # Stop and remove containers
```

Stack runs at:
- Frontend: http://localhost:3000
- Backend:  http://localhost:8001/docs
- MongoDB:  localhost:27017

### Frontend Build-Time Environment Variables

The frontend image is built as a static Nginx bundle, so Vite environment
variables must be passed as Docker build args. Missing values will produce a
fatal Vite config error at build time.

Ensure these variables are present in `.env` and mapped in `docker-compose.yml`
under `services.frontend.build.args`:

- `VITE_AZURE_CLIENT_ID` → sourced from `AZURE_CLIENT_ID`
- `VITE_AZURE_TENANT_ID` → sourced from `AZURE_TENANT_ID`
- `VITE_DEV_AUTH_BYPASS`

`frontend/Dockerfile` declares `ARG VITE_AZURE_CLIENT_ID` and
`ARG VITE_AZURE_TENANT_ID` and exposes them as `ENV` for the Vite build.

## Test Commands

```powershell
# Backend (from backend/)
$env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v

# Frontend unit tests (from frontend/)
npm run test

# Frontend E2E tests (from frontend/)
npm run test:e2e

# Terraform validate (from terraform/)
terraform validate
```

## Claims Dashboard

The target SQL Server `Claims` table is system-versioned (temporal_type = 2)
with a history table at `AuditData.claimsHistory` containing **38.6M+ rows**.

**IMPORTANT:** Dashboard widgets must NOT use `FOR SYSTEM_TIME BETWEEN` —
scanning the 38.6M-row history table causes 504 gateway timeouts (180s) on
cold Azure SQL cache. All temporal widgets were rewritten to use plain
`Claims` queries against the well-indexed current table, plus
`dbo.claims_deleted` for deleted-draft counts. The `FOR SYSTEM_TIME` +
`ROW_NUMBER() OVER (PARTITION BY id)` pattern was replaced with:

- **Drafts Created YTD / Period Comparison**: 3-way `UNION` of current drafts
  (`submitted=0, original_run_id IS NULL`), current runs
  (`submitted=1, original_run_id IS NOT NULL`), and deleted drafts
  (`dbo.claims_deleted`), all filtered by `created BETWEEN`. The `UNION`
  deduplicates by `id`, matching the old `ROW_NUMBER` semantics.
- **Deleted Drafts YTD**: `COUNT(DISTINCT id)` on `dbo.claims_deleted` instead
  of `ROW_NUMBER() OVER (PARTITION BY id ORDER BY [timestamp])`.
- **New Runs / Active Runs / Submitted Period Comparison**: plain `Claims`
  queries with the same filters, no temporal scan.

The `dbo.claims_deleted` table (1M+ rows) is a heap with no useful index
(only `nci_wi_claims_deleted_...` on `(dept_id, run_number)`). Queries against
it do a full scan but complete in <1s on warm cache.

The system-managed `Claims Breakdown` dashboard is built by
`backend/main.py::_build_default_claims_dashboard()` and upserted on every
startup in `backend/main.py::_seed_default_dashboards()`. It defines these
claim buckets:

- **Drafts**: `submitted = 0 AND original_run_id IS NULL`
- **New Runs**: `submitted = 1 AND archived = 0 AND original_run_id IS NOT NULL
  AND ClaimCurrentTypeId = 1`
- **Active Runs**: `submitted = 1 AND archived = 0 AND original_run_id IS NOT NULL
  AND ClaimCurrentTypeId = 4`

`backend/target_db.py` normalizes legacy column names at runtime against live
`Claims` metadata (`_resolve_claims_column_map()`), and resolves the best date
column (`_resolve_claims_date_column()`) by preferring the temporal
`AS_ROW_START` column and falling back to `DateCreated` or similar.
