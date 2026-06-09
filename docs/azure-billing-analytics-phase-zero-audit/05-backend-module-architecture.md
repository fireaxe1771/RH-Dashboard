# Supporting Doc 05 — Backend Module Architecture

**Project:** RecoveryHub Dashboard System  
**Purpose:** Detailed function signatures, module responsibilities, class designs, error handling contracts, scheduler job definitions, and integration patterns for all new backend modules.

---

## 0. Custom Exceptions (`backend/billing/__init__.py`)

Define all billing exceptions here so they can be imported from `billing` by any submodule.

```python
class BillingAPIError(Exception):
    """Raised when an Azure billing API call fails after all retries are exhausted."""
    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code

class BillingConfigError(Exception):
    """Raised when billing configuration is incomplete or invalid."""
    pass

class VectorizerError(Exception):
    """Raised when embedding generation or vector search fails."""
    pass
```

---

## 1. `backend/billing_models.py`

Pydantic v2 models for all billing-related API request/response shapes and internal data. Follow existing Pydantic patterns in `models.py`.

### Request Models

```python
class BillingSyncRequest(BaseModel):
    """Payload to manually trigger a billing sync."""
    sync_type: str = Field(..., description="Type of sync: full, daily, advisor, invoices, reservations, resource_inventory, retail_prices, vectorize")
    billing_period: str | None = Field(None, description="YYYY-MM. Required for period-specific syncs. Ignored for full sync.")

class BillingAIQueryRequest(BaseModel):
    """Payload for a natural language cost analysis query."""
    question: str = Field(..., min_length=5, max_length=1000, description="Natural language question about Azure costs")
    document_types: list[str] | None = Field(None, description="Filter to specific document types. None = all types.")
    period_filter: str | None = Field(None, description="YYYY-MM period to focus on. None = all periods.")
    top_k: int = Field(10, ge=1, le=50, description="Number of context documents to retrieve")
```

### Response Models

```python
class SyncStatusEntry(BaseModel):
    sync_type: str
    status: str
    last_run: datetime | None
    last_period: str | None
    records_synced: int
    duration_seconds: float | None
    error_message: str | None

class SyncStatusResponse(BaseModel):
    syncs: list[SyncStatusEntry]

class CostSummaryItem(BaseModel):
    period: str
    dimension: str
    dimension_value: str
    total_cost: float
    currency: str
    change_pct: float | None
    change_amount: float | None
    record_count: int

class CostSummaryResponse(BaseModel):
    items: list[CostSummaryItem]
    total: float
    currency: str
    period: str

class AdvisorRecommendationItem(BaseModel):
    recommendation_id: str
    category: str
    impact: str
    impacted_value: str
    resource_group: str
    problem_description: str
    solution_description: str
    estimated_monthly_savings: float | None
    savings_currency: str | None
    current_sku: str | None
    recommended_sku: str | None
    last_updated: datetime
    status: str

class AdvisorSummaryResponse(BaseModel):
    total_recommendations: int
    cost_recommendations: int
    total_monthly_savings: float
    currency: str
    by_impact: dict[str, int]

class InvoiceItem(BaseModel):
    invoice_id: str
    billing_period_start: str
    billing_period_end: str
    invoice_date: str
    due_date: str | None
    billed_amount: float
    amount_due: float
    billing_currency: str
    status: str
    invoice_download_url: str | None

class BudgetItem(BaseModel):
    budget_name: str
    scope: str
    amount: float
    current_spend: float
    forecast_spend: float | None
    utilization_pct: float
    time_grain: str
    currency: str

class AIQuerySource(BaseModel):
    document_type: str
    period: str | None
    dimension_value: str | None
    total_cost: float | None
    score: float

class BillingAIQueryResponse(BaseModel):
    answer: str
    sources: list[AIQuerySource]
    model: str
    question: str
```

---

## 2. `backend/billing/auth.py`

### Responsibility
Provides a cached `ClientSecretCredential` and helper functions for acquiring bearer tokens for Azure management plane APIs.

### Implementation

```python
import logging
from functools import lru_cache
from azure.identity import ClientSecretCredential
from config import settings

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def get_billing_credential() -> ClientSecretCredential:
    """Returns a cached ClientSecretCredential for billing API access.
    
    Uses lru_cache to ensure a single credential instance is shared across
    the application, allowing the azure-identity SDK to cache tokens internally.
    Raises BillingConfigError if required settings are missing.
    """
    from billing import BillingConfigError
    if not settings.AZURE_BILLING_CLIENT_ID or not settings.AZURE_BILLING_CLIENT_SECRET:
        raise BillingConfigError(
            "AZURE_BILLING_CLIENT_ID and AZURE_BILLING_CLIENT_SECRET must be set."
        )
    logger.info("Initializing Azure billing service principal credential...")
    return ClientSecretCredential(
        tenant_id=settings.AZURE_TENANT_ID,
        client_id=settings.AZURE_BILLING_CLIENT_ID,
        client_secret=settings.AZURE_BILLING_CLIENT_SECRET
    )

def get_billing_token() -> str:
    """Acquires a fresh bearer token for https://management.azure.com/.
    
    The credential handles token caching and automatic refresh internally.
    Returns the raw token string (without the 'Bearer ' prefix).
    """
    credential = get_billing_credential()
    token = credential.get_token("https://management.azure.com/.default")
    return token.token

def get_billing_auth_headers() -> dict[str, str]:
    """Returns a dict with Authorization and Content-Type headers for API calls."""
    return {
        "Authorization": f"Bearer {get_billing_token()}",
        "Content-Type": "application/json"
    }
```

### Test requirements (`test_billing_auth.py`)
- Mock `ClientSecretCredential` with `unittest.mock.patch`
- Verify `get_billing_credential()` returns a `ClientSecretCredential` instance
- Verify `get_billing_token()` calls `credential.get_token()` with the correct scope
- Verify `BillingConfigError` is raised when client ID is empty

---

## 3. `backend/billing/cost_management.py`

### Responsibility
All calls to the `Microsoft.CostManagement` REST API namespace. Returns structured Python dicts — no MongoDB writes.

### Constants
```python
COST_MANAGEMENT_API_VERSION = "2025-03-01"
MANAGEMENT_BASE = "https://management.azure.com"
MAX_RETRIES = 5
```

### Retry Helper (shared by all billing modules)

```python
async def _api_call_with_retry(session: aiohttp.ClientSession, method: str, url: str, **kwargs) -> dict:
    """Executes an aiohttp request with exponential backoff on 429/503.
    
    Raises BillingAPIError after MAX_RETRIES attempts or on unrecoverable errors.
    """
    from billing import BillingAPIError
    for attempt in range(MAX_RETRIES):
        async with getattr(session, method)(url, **kwargs) as response:
            if response.status == 200:
                return await response.json()
            if response.status == 202:
                return {"status": 202, "location": response.headers.get("Location"), "retry_after": response.headers.get("Retry-After", "30")}
            if response.status in (429, 503):
                retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                wait = min(retry_after, 60)
                logger.warning(f"Rate limited (HTTP {response.status}). Waiting {wait}s before retry {attempt + 1}/{MAX_RETRIES}.")
                await asyncio.sleep(wait)
                continue
            if response.status == 403:
                body = await response.json()
                error_code = body.get("error", {}).get("code", "Unknown")
                raise BillingAPIError(f"Authorization denied (HTTP 403): {error_code}. Verify RBAC roles are assigned.", status_code=403, error_code=error_code)
            if response.status == 404:
                logger.warning(f"Resource not found (HTTP 404) at {url}. Returning empty result.")
                return {}
            body = await response.json()
            error_msg = body.get("error", {}).get("message", "Unknown error")
            raise BillingAPIError(f"API error (HTTP {response.status}): {error_msg}", status_code=response.status)
    raise BillingAPIError(f"Max retries ({MAX_RETRIES}) exceeded for {url}")
```

### Functions

**`async def query_costs(scope, start_date, end_date, granularity, group_by_dimensions) -> list[dict]`**
- POST to `/{scope}/providers/Microsoft.CostManagement/query?api-version=2025-03-01`
- `scope`: e.g. `/subscriptions/{id}`
- `start_date`, `end_date`: `str` in `YYYY-MM-DD` format
- `granularity`: `"None"`, `"Daily"`, `"Monthly"`
- `group_by_dimensions`: `list[str]` e.g. `["ServiceName", "ResourceGroupName"]`
- Returns: `list[dict]` where each dict is a row with dimension values and cost amount
- Handles `nextLink` pagination automatically

**`async def generate_cost_details_report(scope, start_date, end_date, metric) -> list[dict]`**
- POST submit → poll loop → download CSV → parse → return
- `metric`: `"ActualCost"` or `"AmortizedCost"`
- Poll interval: 30 seconds
- Poll timeout: 15 minutes
- Returns: `list[dict]` with keys matching the CSV column names (snake_case converted)
- For blobs > 100MB: use `aiohttp` streaming and write to a temp file, then parse the temp file
- After download, delete temp file if used

**`async def get_forecast(scope, start_date, end_date) -> list[dict]`**
- POST to `/{scope}/providers/Microsoft.CostManagement/forecast`
- Returns daily/monthly cost predictions

**`async def get_budgets(scope) -> list[dict]`**
- GET all budgets for the scope
- Returns list of budget dicts (raw from API)
- Returns `[]` if no budgets (404 → empty list)

**`async def get_alerts(scope) -> list[dict]`**
- GET all alerts for the scope
- Returns `[]` if no alerts

---

## 4. `backend/billing/consumption.py`

### Functions

**`async def get_reservation_details(scope, start_date, end_date) -> list[dict]`**
- GET from Consumption API
- `scope` should be the billing account scope for EA

**`async def get_reservation_summaries(scope, grain) -> list[dict]`**
- `grain`: `"Daily"` or `"Monthly"`

**`async def get_reservation_recommendations(scope, term, look_back_period) -> list[dict]`**
- `term`: `"P1Y"` or `"P3Y"`
- `look_back_period`: `"Last7Days"`, `"Last30Days"`, `"Last60Days"`
- Pull recommendations for both P1Y and P3Y, both Last30Days and Last60Days

**`async def get_marketplace_charges(scope, start_date, end_date) -> list[dict]`**

**`async def get_price_sheet(scope, billing_period) -> list[dict]`**
- EA/MCA only — handle `403` gracefully (return `[]` with a warning)
- Paginate with `$skiptoken`
- Do not attempt for MOSP accounts

---

## 5. `backend/billing/advisor.py`

### Functions

**`async def get_recommendations(subscription_id, category) -> list[dict]`**
- GET `/subscriptions/{id}/providers/Microsoft.Advisor/recommendations?api-version=2025-01-01&$filter=Category eq '{category}'`
- Handles `nextLink` pagination
- `category`: `"Cost"`, `"Security"`, `"Performance"`, `"HighAvailability"`, `"OperationalExcellence"`

**`async def get_all_recommendations(subscription_id) -> list[dict]`**
- Calls `get_recommendations()` for all 5 categories in sequence (respect rate limits — 1s pause between categories)
- Returns combined list

**Helper: `_extract_savings(extended_properties) -> tuple[float | None, str | None]`**
- Parses `savingsAmount` and `savingsCurrency` from `extendedProperties`
- Returns `(estimated_monthly_savings, savings_currency)` or `(None, None)`

---

## 6. `backend/billing/billing_accounts.py`

### Functions

**`async def get_billing_accounts() -> list[dict]`**
- GET `/providers/Microsoft.Billing/billingAccounts?api-version=2020-05-01`

**`async def get_billing_periods(subscription_id) -> list[dict]`**
- GET billing periods for a subscription
- Returns list sorted by date descending

**`async def get_invoices(billing_account_id, billing_account_type) -> list[dict]`**
- Routes to the correct endpoint based on `billing_account_type`
- EA: `/providers/Microsoft.Billing/billingAccounts/{id}/invoices`
- MCA: `/providers/Microsoft.Billing/billingAccounts/{id}/billingProfiles/{profileId}/invoices`
- MOSP: `/subscriptions/{subscriptionId}/providers/Microsoft.Billing/invoices`

**`async def get_invoice_transactions(billing_account_id, invoice_id) -> list[dict]`**
- MCA/MPA only — returns `[]` for EA/MOSP with a warning log

---

## 7. `backend/billing/resource_graph.py`

### Functions

**`async def query_resources(subscription_ids, kql_query, page_size) -> list[dict]`**
- POST to `/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01`
- `subscription_ids`: `list[str]`
- `page_size`: int, default 1000
- Handles `$skipToken` pagination
- Returns combined list from all pages

**Predefined KQL query constants:**
```python
KQL_ALL_RESOURCES = """
Resources
| project id, name, type, location, resourceGroup, subscriptionId, sku, tags, kind, properties
| where type !startswith 'microsoft.resources/deployments'
| order by type asc
"""

KQL_DEALLOCATED_VMS = """
Resources
| where type == 'microsoft.compute/virtualmachines'
| extend powerState = tostring(properties.extended.instanceView.powerState.code)
| where powerState =~ 'PowerState/deallocated' or powerState =~ 'PowerState/stopped'
| project id, name, resourceGroup, location, skuName = sku.name, powerState, tags
"""

KQL_UNTAGGED_RESOURCES = """
Resources
| where isnull(tags) or array_length(bag_keys(tags)) == 0
| where type !startswith 'microsoft.resources'
| project id, name, type, resourceGroup, location
"""
```

---

## 8. `backend/billing/retail_prices.py`

### Functions

**`async def get_retail_prices(service_name, arm_region_name, currency_code) -> list[dict]`**
- GET `https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&$filter=...`
- No auth header needed
- Follows `NextPageLink` pagination
- `service_name`: optional filter (e.g. `"Virtual Machines"`)
- `arm_region_name`: optional filter (e.g. `"eastus"`)

**`async def sync_common_service_prices() -> list[dict]`**
- Fetches retail prices for the most commonly billed Azure services
- Service list: `Virtual Machines`, `Azure App Service`, `Azure SQL Database`, `Azure Cosmos DB`, `Azure Kubernetes Service`, `Azure Blob Storage`, `Azure Container Apps`, `Azure Monitor`
- Limits to `eastus` region to keep volume manageable
- Returns combined list

---

## 9. `backend/billing/sync_service.py`

### Responsibility
Orchestrates all sync operations. Reads from Azure APIs via the billing modules. Writes to MongoDB via Motor. Manages the sync log.

### Helper: `_write_sync_log_start(db, sync_type, billing_period, triggered_by) -> str`
Creates a `azure_billing_sync_log` entry with `status="running"`. Returns the inserted `_id` as string.

### Helper: `_write_sync_log_complete(db, log_id, records_synced, records_skipped)`
Updates the log entry to `status="completed"` with counts and duration.

### Helper: `_write_sync_log_failed(db, log_id, error_message)`
Updates the log entry to `status="failed"`.

### Helper: `_rebuild_cost_summary(db, billing_period)`
Queries `azure_cost_details` and aggregates by period + dimension. Upserts into `azure_cost_summary`. Runs after each `sync_cost_details()` call.

### Core Functions

**`async def sync_cost_details(db, billing_period, triggered_by) -> int`**

```
1. Write sync log entry (status: running)
2. Determine scope from settings (subscription scope)
3. Determine start/end dates from billing_period string
4. Call cost_management.generate_cost_details_report(scope, start, end, "ActualCost")
5. For each row in CSV:
   a. Parse and clean fields (snake_case, type coercions, tags JSON parse)
   b. Upsert to azure_cost_details with dedup key
6. Also call generate_cost_details_report with "AmortizedCost" for reservation analysis
7. Call _rebuild_cost_summary(db, billing_period)
8. Update sync log (status: completed, records_synced count)
9. Return record count
```

**`async def run_full_backfill(db, months, triggered_by) -> dict`**

```
1. Check if azure_cost_details has any documents — if yes, log and return (already done)
2. Compute list of billing_periods for the last `months` months
3. For each period (oldest first):
   a. Call sync_cost_details(db, period, triggered_by="startup_backfill")
   b. Sleep 5s between periods (rate limit respect)
4. Call sync_advisor_recommendations()
5. Call sync_budgets()
6. Call sync_invoices()
7. Call sync_reservations()
8. Call sync_resource_inventory()
9. Call sync_retail_prices()
Return summary dict with record counts per sync type
```

**`async def run_daily_sync(db) -> dict`**

```
1. Sync current month cost details
2. Sync previous month cost details (for late-arriving data)
3. Sync budgets (fast, do every day)
4. Sync alerts (fast, do every day)
5. Return summary
```

**`async def sync_advisor_recommendations(db, triggered_by) -> int`**

```
1. Call advisor.get_all_recommendations(settings.AZURE_SUBSCRIPTION_ID)
2. For each recommendation:
   a. Map fields to azure_advisor_recommendations schema
   b. Extract savings from extended_properties
   c. Upsert by recommendation_id
3. Mark stale recommendations: set status="Inactive" for recommendation_ids that were
   previously Active but not returned in this sync
4. Return upserted count
```

**`async def sync_budgets(db, triggered_by) -> int`**

**`async def sync_invoices(db, triggered_by) -> int`**

**`async def sync_reservations(db, triggered_by) -> int`**
- Calls both reservation_details and reservation_recommendations

**`async def sync_resource_inventory(db, triggered_by) -> int`**
- Calls resource_graph.query_resources() with KQL_ALL_RESOURCES + KQL_DEALLOCATED_VMS

**`async def sync_retail_prices(db, triggered_by) -> int`**

---

## 10. `backend/billing/scheduler.py`

### Responsibility
APScheduler `AsyncIOScheduler` configuration and job definitions.

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import settings
from database import get_db

billing_scheduler = AsyncIOScheduler(timezone="UTC")

def setup_billing_jobs() -> None:
    """Registers all billing sync jobs on the scheduler."""
    
    # Daily cost + budget sync at configured hour (default 2:00 AM UTC)
    billing_scheduler.add_job(
        _daily_sync_job,
        CronTrigger(hour=settings.BILLING_DAILY_SYNC_HOUR, minute=0),
        id="billing_daily_sync",
        name="Azure Billing Daily Sync",
        replace_existing=True,
        misfire_grace_time=3600  # Allow 1hr grace if app was down at trigger time
    )

    # Advisor recommendations — every Monday at 3:00 AM UTC
    billing_scheduler.add_job(
        _advisor_sync_job,
        CronTrigger(day_of_week="mon", hour=3, minute=0),
        id="billing_advisor_sync",
        name="Azure Advisor Recommendations Sync",
        replace_existing=True,
        misfire_grace_time=3600
    )

    # Reservations — every Monday at 3:30 AM UTC
    billing_scheduler.add_job(
        _reservation_sync_job,
        CronTrigger(day_of_week="mon", hour=3, minute=30),
        id="billing_reservation_sync",
        name="Azure Reservation Sync",
        replace_existing=True,
        misfire_grace_time=3600
    )

    # Invoices — 5th of each month at 4:00 AM UTC
    billing_scheduler.add_job(
        _invoice_sync_job,
        CronTrigger(day=5, hour=4, minute=0),
        id="billing_invoice_sync",
        name="Azure Invoice Sync",
        replace_existing=True,
        misfire_grace_time=86400
    )

    # Resource inventory — every Sunday at 1:00 AM UTC
    billing_scheduler.add_job(
        _resource_inventory_job,
        CronTrigger(day_of_week="sun", hour=1, minute=0),
        id="billing_resource_inventory",
        name="Azure Resource Inventory Sync",
        replace_existing=True,
        misfire_grace_time=3600
    )
```

Each `_*_job` function is a thin wrapper that acquires a DB reference and calls the corresponding `sync_service` function. Example:
```python
async def _daily_sync_job() -> None:
    """Scheduled wrapper for daily billing sync."""
    db = db_manager.db
    if db is None:
        logger.error("Scheduler: DB not available, skipping daily sync.")
        return
    try:
        result = await sync_service.run_daily_sync(db)
        logger.info(f"Daily billing sync completed: {result}")
        # Trigger vectorization after successful sync
        await vectorizer.run_vectorization(db)
    except Exception as e:
        logger.error(f"Daily billing sync failed: {e}")
```

---

## 11. `backend/billing_routes.py`

### Structure
Single FastAPI `APIRouter` with prefix handled at mount time in `main.py`.

```python
from fastapi import APIRouter, Depends, HTTPException, status
from database import get_db
from auth import get_current_user
from billing import sync_service
from billing_models import (
    BillingSyncRequest, BillingAIQueryRequest,
    SyncStatusResponse, CostSummaryResponse, ...
)

billing_router = APIRouter(tags=["Azure Billing"])
```

### Endpoint Specifications

**`GET /sync/status`**
- Query `azure_billing_sync_log` grouped by `sync_type` — return latest entry per type
- Response: `SyncStatusResponse`

**`POST /sync/trigger`**
- Body: `BillingSyncRequest`
- Validates `sync_type` is one of the allowed values
- Dispatches sync as a background task using `fastapi.BackgroundTasks`
- Returns immediately: `{"status": "queued", "sync_type": sync_type}`
- Do NOT await the sync (it takes minutes) — use `background_tasks.add_task()`

**`GET /cost/summary`**
- Query params: `period: str` (YYYY-MM, default: current month), `dimension: str` (default: `ServiceName`)
- Query `azure_cost_summary` collection
- Response: `CostSummaryResponse`

**`GET /cost/trend`**
- Query params: `months: int` (default 12), `dimension: str` (default `ServiceName`), `dimension_value: str | None`
- Returns month-over-month cost data for trend charts

**`GET /cost/top-spenders`**
- Query params: `period: str`, `dimension: str`, `limit: int` (default 10)
- Returns top N by `total_cost` descending

**`GET /cost/by-tag`**
- Query params: `period: str`, `tag_key: str`, `limit: int`
- Aggregates `azure_cost_details` by tag value for the given tag key

**`GET /cost/daily`**
- Query params: `start_date: str`, `end_date: str`, `service_name: str | None`
- Aggregates `azure_cost_details` by date for date-range charts

**`GET /cost/forecast`**
- Returns documents from `azure_cost_summary` where `dimension == "Forecast"`
- Or calls live Forecast API if no cached data is available

**`GET /budgets`**
- Returns all documents from `azure_budgets` sorted by `utilization_pct` descending

**`GET /alerts`**
- Returns all `Active` documents from `azure_cost_alerts` sorted by `creation_time` descending

**`GET /advisor/recommendations`**
- Query params: `category: str | None`, `impact: str | None`, `status: str` (default "Active")

**`GET /advisor/cost-savings`**
- Returns Cost category recommendations sorted by `estimated_monthly_savings` descending

**`GET /advisor/summary`**
- Returns aggregated savings totals and counts from `azure_advisor_recommendations`

**`GET /invoices`**
- Returns all invoices sorted by `billing_period_start` descending

**`GET /invoices/{invoice_id}`**
- Returns one invoice by invoice_id

**`GET /reservations/details`**
- Query params: `billing_period: str`

**`GET /reservations/recommendations`**
- Sorted by `net_savings` descending

**`POST /ai/query`**
- Body: `BillingAIQueryRequest`
- See Supporting Doc 06 for the full processing pipeline
- Response: `BillingAIQueryResponse`

---

## 12. `backend/main.py` Modifications

### Import additions (at top of file):
```python
from billing.scheduler import billing_scheduler, setup_billing_jobs
from billing.sync_service import run_full_backfill
from billing_routes import billing_router
```

### Lifespan modifications (within the existing `lifespan()` context manager):
Add after `await _seed_default_dashboards()`:
```python
# Start billing sync scheduler if enabled
if settings.BILLING_SYNC_ENABLED:
    setup_billing_jobs()
    billing_scheduler.start()
    logger.info("Billing sync scheduler started.")
    # Run initial backfill in background (no-op if already populated)
    asyncio.create_task(_run_billing_backfill_if_needed())
```

Add to the cleanup (after `yield`):
```python
if settings.BILLING_SYNC_ENABLED and billing_scheduler.running:
    billing_scheduler.shutdown(wait=False)
    logger.info("Billing sync scheduler stopped.")
```

### New helper function:
```python
async def _run_billing_backfill_if_needed() -> None:
    """Checks if billing data exists; runs full backfill if not. Background task."""
    try:
        db = db_manager.db
        count = await db["azure_cost_details"].count_documents({})
        if count == 0:
            logger.info("No billing data found. Starting historical backfill...")
            await run_full_backfill(db, settings.BILLING_HISTORY_MONTHS, "startup_backfill")
    except Exception as e:
        logger.error(f"Billing backfill check failed: {e}")
```

### Router mount (after existing router mounts):
```python
app.include_router(billing_router, prefix="/api/billing")
```
