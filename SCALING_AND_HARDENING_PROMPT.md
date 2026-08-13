# Task: Terraform Scaling + Data Slave Process Hardening — RecoveryHub Dashboard

You are working on the RecoveryHub Dashboard project at `E:\gitrepo\RH Dashboard`.
This is a FastAPI backend + React/TypeScript frontend deployed to Azure
Container Apps. The backend runs a billing sync scheduler (APScheduler) that
periodically calls Azure APIs and writes results to MongoDB.

Your job is to fix five specific issues identified in a production readiness
review. Implement every fix listed below. Do NOT skip any. After all fixes
are implemented, run the test suites to verify nothing broke.

## Project context (read these files first to understand the codebase)

- `AGENTS.md` — project notes, build/test commands, deploy notes
- `terraform/main.tf` — Container Apps infra (backend + frontend resources)
- `terraform/variables.tf` — Terraform variables
- `backend/main.py` — FastAPI app, lifespan (scheduler start/stop at ~line 40-60)
- `backend/billing/sync_service.py` — sync orchestration (8 sync functions + 2 composite)
- `backend/billing/scheduler.py` — APScheduler job definitions and wrappers
- `backend/billing/vectorizer.py` — generates text documents from billing data for AI embedding
- `backend/billing_routes.py` — billing API endpoints (manual sync trigger + read endpoints)
- `backend/tests/conftest.py` — test fixtures (mock_mongo_db, async mongomock wrappers)
- `backend/tests/test_billing_sync_service.py` — existing sync service tests
- `backend/tests/test_billing_scheduler.py` — existing scheduler tests

## Build & test commands

```powershell
# Backend tests (from backend/)
$env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v

# Terraform validate (from terraform/)
terraform validate
```

---

## FIX T1 (CRITICAL): Frontend min_replicas must be 1, not 0

**File:** `terraform/main.tf` (around line 301-303)

**Current code:**
```hcl
    # SCALE TO ZERO RULES
    min_replicas = 0
    max_replicas = 5
```

**Problem:** The frontend Container App scales to zero. The user explicitly
requires all production container apps to scale to a minimum of 1 replica.
Scale-to-zero also causes cold-start latency on first load that compounds
with MSAL redirect timing, contributing to "quirky" auth behavior.

**Fix:** Change `min_replicas` from `0` to `1` and update the comment:
```hcl
    # Minimum 1 replica ensures the app is always warm (no cold-start
    # latency that would compound with MSAL redirect timing).
    min_replicas = 1
    max_replicas = 5
```

---

## FIX T2 (HIGH): Backend container is undersized for its workload

**File:** `terraform/main.tf` (around line 78-79, inside the backend
`template { container { ... } }` block)

**Current code:**
```hcl
      cpu    = "0.25"
      memory = "0.5Gi"
```

**Problem:** The backend runs FastAPI (4 Uvicorn workers per the Dockerfile)
plus APScheduler (5 scheduled jobs) plus billing sync jobs (Azure API calls,
MongoDB writes, AI vectorization). 0.25 vCPU / 0.5Gi is the same sizing as
the static Nginx frontend. Combined with unbounded Mongo queries (FIX S2
below), this will cause OOM during backfill or CPU throttling during
concurrent API requests.

**Fix:** Increase to 0.5 vCPU / 1.0Gi:
```hcl
      cpu    = "0.5"
      memory = "1.0Gi"
```

---

## FIX S1 (CRITICAL): Add concurrency locks to prevent overlapping sync jobs

**Files:**
- `backend/billing/sync_service.py` — all 8 `sync_*` functions + `run_daily_sync` + `run_full_backfill`
- `backend/billing_routes.py` — manual sync trigger at ~line 88-103

**Problem:** There is no locking mechanism. A manual sync triggered via the
API at 2:05 AM can collide with the 2:00 AM scheduled daily sync. Both write
to the same MongoDB collections simultaneously, causing:
- Duplicate API calls to Azure (rate-limit exhaustion)
- Race conditions in sync log updates
- MongoDB write conflicts

**Fix:**

1. At the top of `backend/billing/sync_service.py` (after the imports,
   before the helper functions), add a module-level lock dictionary:

```python
# --------------------------------------------------------------------------- #
# Concurrency control — prevents overlapping sync runs of the same type.
# A scheduled sync and a manual API trigger for the same sync_type cannot
# run simultaneously. The lock is per sync_type (not global) so different
# sync types can run in parallel.
# --------------------------------------------------------------------------- #
_sync_locks: dict[str, asyncio.Lock] = {}


def _get_sync_lock(sync_type: str) -> asyncio.Lock:
    """Returns (creating if needed) the asyncio.Lock for the given sync type."""
    if sync_type not in _sync_locks:
        _sync_locks[sync_type] = asyncio.Lock()
    return _sync_locks[sync_type]
```

2. Wrap each of the 8 `sync_*` functions with the lock. The lock key should
   be the sync_type string used in the sync log. For `sync_cost_details`,
   include the billing_period in the lock key so different periods can sync
   in parallel. Example pattern for `sync_cost_details`:

```python
async def sync_cost_details(db, billing_period: str, triggered_by: str = "manual_api") -> int:
    """Syncs unaggregated cost line items for one billing period, then rebuilds summaries."""
    lock = _get_sync_lock(f"cost_details_{billing_period}")
    if lock.locked():
        logger.warning(f"Sync cost_details_{billing_period} already in progress, skipping.")
        return 0
    async with lock:
        log_id = await _write_sync_log_start(db, "cost_details_daily", billing_period, triggered_by)
        try:
            # ... existing sync logic unchanged ...
            return count
        except Exception as exc:
            await _write_sync_log_failed(db, log_id, str(exc))
            raise
```

   For the other 7 sync functions (which don't take a billing_period), use
   the sync_type string as the lock key:
   - `sync_advisor_recommendations` → lock key `"advisor"`
   - `sync_budgets` → lock key `"budgets"`
   - `sync_alerts` → lock key `"alerts"`
   - `sync_invoices` → lock key `"invoices"`
   - `sync_reservations` → lock key `"reservations"`
   - `sync_resource_inventory` → lock key `"resource_inventory"`
   - `sync_retail_prices` → lock key `"retail_prices"`

   Apply the same `if lock.locked(): logger.warning(...); return 0` guard
   and `async with lock:` wrapper to each. Do NOT change the function
   signatures or any logic inside the sync bodies — only wrap them.

3. For `run_daily_sync` and `run_full_backfill`, these are composite
   functions that call the individual sync functions. Since the individual
   functions now have their own locks, the composite functions don't need
   their own locks — the per-sync locks will prevent overlaps at the
   individual level. Leave `run_daily_sync` and `run_full_backfill` as-is.

**Important:** The `if lock.locked(): return 0` guard is a non-blocking
skip — it does NOT wait. This is intentional: if a scheduled sync is running
and a manual trigger comes in for the same type, the manual trigger should
skip (returning 0) rather than queue up and wait, which could cause the
HTTP request to time out. Log a warning so the skip is visible.

---

## FIX S2 (CRITICAL): Stream unbounded MongoDB queries to prevent OOM

**Files:**
- `backend/billing/sync_service.py` — `_rebuild_cost_summary` at ~line 213-214
- `backend/billing/vectorizer.py` — 6 sites at lines ~55, 67, 130, 169, 204, 243
- `backend/billing_routes.py` — 13 read endpoints (lines ~118, 135, 159, 180, 190, 200, 206, 226, 234, 244, 271, 292, 298)
- `backend/ai_analytics/mongo_repository.py` — 3 sites at lines ~100, 189, 242
- `backend/ai_analytics/diagnostics_service.py` — 1 site at line ~313

**Problem:** 28 call sites use `.to_list(length=None)` which loads entire
collections into memory. On a real billing period with thousands of cost
line items, this will OOM the 0.5Gi (now 1.0Gi after FIX T2) backend
container.

**Fix strategy:** The fix differs by context. Read each call site and apply
the appropriate approach:

### Category A: Read endpoints in `billing_routes.py` (API responses)

For these, the simplest and safest fix is to add a reasonable `length` cap
to prevent unbounded memory growth. These are HTTP API responses that
shouldn't return tens of thousands of rows anyway. Replace
`to_list(length=None)` with `to_list(length=1000)` at these sites:
- Line ~118 (cost_summary)
- Line ~135 (cost_trend)
- Line ~159 (cost_by_tag)
- Line ~180 (cost_daily)
- Line ~190 (cost_forecast)
- Line ~200 (list_budgets)
- Line ~206 (list_alerts)
- Line ~226 (advisor_recommendations)
- Line ~234 (advisor_cost_savings)
- Line ~244 (advisor_summary)
- Line ~271 (list_invoices)
- Line ~292 (reservation_details)
- Line ~298 (reservation_recommendations)

Add a brief comment at the top of the file or near the first occurrence:
```python
# All billing read endpoints cap at 1000 rows to prevent unbounded memory
# consumption. If a collection grows beyond this, pagination should be added.
```

### Category B: `_rebuild_cost_summary` in `sync_service.py` (line ~213-214)

This loads ALL cost details for a billing period to aggregate them. This is
the most dangerous site because a single billing period can have tens of
thousands of line items. Replace the single `to_list(length=None)` with
batched cursor streaming:

**Current code (lines ~205-241):**
```python
async def _rebuild_cost_summary(db, billing_period: str) -> None:
    """Aggregates azure_cost_details by dimension and upserts azure_cost_summary."""
    dimensions = {
        "ServiceName": "service_name",
        "ResourceGroupName": "resource_group",
        "Location": "location",
        "ChargeType": "charge_type",
    }
    cursor = db["azure_cost_details"].find({"billing_period": billing_period})
    rows = await cursor.to_list(length=None)

    for dimension, field in dimensions.items():
        buckets: dict[str, dict] = {}
        for row in rows:
            value = row.get(field) or "Unknown"
            bucket = buckets.setdefault(value, {"total_cost": 0.0, "usage_quantity": 0.0, "count": 0, "currency": row.get("billing_currency", "USD")})
            bucket["total_cost"] += _to_float(row.get("pre_tax_cost"))
            bucket["usage_quantity"] += _to_float(row.get("quantity"))
            bucket["count"] += 1

        for value, bucket in buckets.items():
            key = {
                "period": billing_period,
                "subscription_id": settings.AZURE_SUBSCRIPTION_ID,
                "dimension": dimension,
                "dimension_value": value,
            }
            summary = {
                **key,
                "total_cost": round(bucket["total_cost"], 4),
                "currency": bucket["currency"],
                "usage_quantity": round(bucket["usage_quantity"], 4),
                "unit_of_measure": "",
                "record_count": bucket["count"],
                "sync_timestamp": _now(),
            }
            await db["azure_cost_summary"].update_one(key, {"$set": summary}, upsert=True)
```

**Replace with batched streaming:**
```python
async def _rebuild_cost_summary(db, billing_period: str) -> None:
    """Aggregates azure_cost_details by dimension and upserts azure_cost_summary.

    Streams cost details in batches of 1000 to avoid loading an entire billing
    period (potentially tens of thousands of rows) into memory at once.
    """
    dimensions = {
        "ServiceName": "service_name",
        "ResourceGroupName": "resource_group",
        "Location": "location",
        "ChargeType": "charge_type",
    }

    # Accumulate buckets across all dimensions in a single pass through the
    # cursor, so we only stream the collection once regardless of dimension count.
    # Structure: { dimension: { dimension_value: {total_cost, usage_quantity, count, currency} } }
    all_buckets: dict[str, dict[str, dict]] = {
        dim: {} for dim in dimensions
    }

    BATCH_SIZE = 1000
    cursor = db["azure_cost_details"].find({"billing_period": billing_period})
    while True:
        batch = await cursor.to_list(length=BATCH_SIZE)
        if not batch:
            break
        for row in batch:
            for dimension, field in dimensions.items():
                value = row.get(field) or "Unknown"
                bucket = all_buckets[dimension].setdefault(value, {
                    "total_cost": 0.0,
                    "usage_quantity": 0.0,
                    "count": 0,
                    "currency": row.get("billing_currency", "USD"),
                })
                bucket["total_cost"] += _to_float(row.get("pre_tax_cost"))
                bucket["usage_quantity"] += _to_float(row.get("quantity"))
                bucket["count"] += 1

    for dimension, buckets in all_buckets.items():
        for value, bucket in buckets.items():
            key = {
                "period": billing_period,
                "subscription_id": settings.AZURE_SUBSCRIPTION_ID,
                "dimension": dimension,
                "dimension_value": value,
            }
            summary = {
                **key,
                "total_cost": round(bucket["total_cost"], 4),
                "currency": bucket["currency"],
                "usage_quantity": round(bucket["usage_quantity"], 4),
                "unit_of_measure": "",
                "record_count": bucket["count"],
                "sync_timestamp": _now(),
            }
            await db["azure_cost_summary"].update_one(key, {"$set": summary}, upsert=True)
```

**Note on the test mock:** The test fixture in `conftest.py` provides an
`_AsyncCursor` wrapper (line ~32-42) whose `to_list(length=None)` returns
all items and `to_list(length=N)` returns `items[:N]`. The batched loop
`while True: batch = await cursor.to_list(length=BATCH_SIZE); if not batch: break`
will work correctly with this mock: it returns all items in the first batch
(since the mock doesn't actually paginate), then returns `[]` on the next
call (the underlying mongomock cursor is exhausted). Verify this works by
running the existing `test_sync_cost_details_upserts_and_logs` test — it
calls `sync_cost_details` which calls `_rebuild_cost_summary`.

### Category C: `vectorizer.py` (6 sites)

These generate text documents from billing data for AI embedding. They
load entire collections. Apply the same `to_list(length=1000)` cap as
Category A for these sites. These are document-generation functions, not
API responses, but capping at 1000 is still reasonable — if there are more
than 1000 summary rows for a period, vectorizing all of them would be
prohibitively expensive for the AI embedding step anyway.

Replace `to_list(length=None)` with `to_list(length=1000)` at:
- Line ~55 (`_generate_cost_documents`: service_rows)
- Line ~67 (`_generate_cost_documents`: rg_rows)
- Line ~130 (advisor recommendations)
- Line ~169 (budgets)
- Line ~204 (reservation recommendations)
- Line ~243 (any remaining site)

### Category D: `ai_analytics/mongo_repository.py` (3 sites) and `diagnostics_service.py` (1 site)

Replace `to_list(length=None)` with `to_list(length=1000)` at:
- `mongo_repository.py` line ~100
- `mongo_repository.py` line ~189
- `mongo_repository.py` line ~242
- `diagnostics_service.py` line ~313

---

## FIX S3 (CRITICAL): Scheduler shutdown must wait for in-flight jobs

**File:** `backend/main.py` (lines ~57-59)

**Current code:**
```python
    # Clean disconnect on shutdown
    if settings.BILLING_SYNC_ENABLED and billing_scheduler.running:
        billing_scheduler.shutdown(wait=False)
        logger.info("Billing sync scheduler stopped.")
    db_manager.disconnect()
```

**Problem:** `wait=False` means in-flight sync jobs are abandoned
mid-execution on container restart/deploy. This leaves MongoDB in a
partial state (half-written sync) and sync log entries stuck in "running"
status forever.

**Fix:** Change to `wait=True` so in-flight jobs complete before shutdown:

```python
    # Clean disconnect on shutdown
    if settings.BILLING_SYNC_ENABLED and billing_scheduler.running:
        logger.info("Shutting down billing scheduler, waiting for in-flight jobs to complete...")
        billing_scheduler.shutdown(wait=True)
        logger.info("Billing sync scheduler stopped cleanly.")
    db_manager.disconnect()
```

**Note:** `wait=True` blocks until running jobs finish. In a Container Apps
deployment, the SIGTERM grace period is typically 300 seconds. If a sync
job takes longer than that, the platform will force-kill the container
anyway — but `wait=True` at least gives in-flight jobs a chance to complete
gracefully rather than being immediately abandoned. This is strictly better
than `wait=False`.

---

## After all fixes: verify

1. Run backend tests:
   ```powershell
   cd backend
   $env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v
   ```
   All existing tests must still pass. Pay special attention to:
   - `test_billing_sync_service.py` — verifies the sync functions still work
   - `test_billing_scheduler.py` — verifies scheduler behavior
   - `test_billing_routes.py` and `test_billing_routes_extra.py` — verifies
     the read endpoints still return data (the `to_list(length=1000)` change
     must not break these)

   If any test fails, fix the issue and re-run until all pass.

2. Run Terraform validate:
   ```powershell
   cd terraform
   terraform validate
   ```
   Must return "Success!"

3. Check for any remaining `to_list(length=None)` in the codebase that
   should have been changed:
   ```powershell
   cd backend
   # Search for remaining unbounded queries (test files are OK to leave as-is)
   Select-String -Path "*.py","billing\*.py","ai_analytics\*.py" -Pattern "to_list\(length=None\)" -Recurse
   ```
   The only remaining hits should be in `tests/` files (which use the mock
   cursor and are fine). If any non-test file still has `to_list(length=None)`,
   fix it.

## Constraints

- Do NOT change any function signatures or public API surfaces.
- Do NOT change any logic inside the sync function bodies (FIX S1 only wraps
  them with a lock — the sync logic itself is unchanged).
- Do NOT remove existing comments unless directly replacing them.
- Do NOT add emojis to code or comments.
- Follow existing code style in each file (indentation, quoting, etc.).
- Do NOT push, commit, or stage any changes. Leave all changes unstaged in
  the working tree for review. Do NOT run `git add`, `git commit`, or `git push`.
- The `_AsyncCursor` mock in `conftest.py` must continue to work. If the
  batched streaming loop in FIX S2 breaks the mock, adjust the mock in
  `conftest.py` to support repeated `to_list(length=N)` calls (returning
  successive batches). However, try the fix first without changing the
  mock — the existing implementation should handle it correctly since
  mongomock cursors are single-pass and return `[]` once exhausted.
