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
