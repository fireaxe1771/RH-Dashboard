# Supporting Doc 04 — MongoDB Schema Design

**Project:** RecoveryHub Dashboard System  
**Purpose:** Complete MongoDB collection schemas, field definitions, index specifications, upsert keys, and the Atlas Vector Search index JSON.

---

## General Rules

- All collections live in the existing database `recoveryhub_dashboard` (controlled by `MONGODB_DB_NAME` env var).
- All collection names use `snake_case` with the prefix `azure_billing_` or `azure_`.
- All `_id` fields are MongoDB `ObjectId` — serialized to `str` in API responses.
- All `datetime` fields store UTC ISO 8601 timestamps (`datetime` Python objects, stored as MongoDB `Date`).
- All monetary values are stored as `float` (Python) / `Double` (MongoDB).
- `sync_timestamp` is present on every collection document — it is the UTC datetime when the record was last written by a sync job.
- Index creation belongs exclusively in `database.py` `init_indexes()`.

---

## Collection 1: `azure_cost_details`

**Purpose:** Unaggregated cost line items — one record per resource+meter+date combination. The raw billing data.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "billing_period": str,          # "YYYY-MM" format e.g. "2026-05"
    "date": str,                    # "YYYY-MM-DD" format e.g. "2026-05-15"
    "subscription_id": str,
    "subscription_name": str,
    "resource_group": str,
    "resource_id": str,             # Full ARM resource ID
    "resource_name": str,           # Short name extracted from resource_id
    "service_name": str,            # e.g. "Virtual Machines"
    "service_family": str,          # e.g. "Compute"
    "meter_category": str,
    "meter_subcategory": str,
    "meter_name": str,
    "meter_id": str,
    "quantity": float,
    "unit_of_measure": str,
    "unit_price": float,
    "effective_price": float,       # After EA/MCA discount
    "pre_tax_cost": float,          # The actual cost amount (mapped from CSV "Cost" column)
    "pay_go_price": float,          # Retail price for comparison
    "billing_currency": str,        # "USD"
    "tags": dict,                   # Parsed from JSON string in CSV (key-value pairs)
    "location": str,                # Azure region
    "consumed_service": str,        # e.g. "Microsoft.Compute"
    "charge_type": str,             # "Usage", "Purchase", "Tax", "Credit"
    "publisher_name": str,
    "publisher_type": str,          # "Azure", "Marketplace", "AWS"
    "pricing_model": str,           # "OnDemand", "Reservation", "SavingsPlan"
    "reservation_id": str | None,
    "reservation_name": str | None,
    "benefit_id": str | None,
    "benefit_name": str | None,
    "invoice_id": str | None,
    "is_azure_credit_eligible": bool,
    "frequency": str,               # "UsageBased", "Recurring", "OneTime"
    "term": str | None,             # e.g. "P1Y" for reservations
    "product_name": str,
    "part_number": str,
    "sku_id": str | None,
    "plan_name": str | None,
    "additional_info": dict,        # Parsed from JSON string
    "sync_timestamp": datetime,
    "data_source": str,             # "cost_details_api"
}
```

**Upsert Key (unique identifier for deduplication):**
```python
{
    "subscription_id": record["subscription_id"],
    "date": record["date"],
    "resource_id": record["resource_id"],
    "meter_id": record["meter_id"],
    "charge_type": record["charge_type"]
}
```

**Indexes:**
```python
await db["azure_cost_details"].create_index([("billing_period", 1), ("subscription_id", 1)])
await db["azure_cost_details"].create_index([("date", 1)])
await db["azure_cost_details"].create_index([("service_name", 1), ("billing_period", 1)])
await db["azure_cost_details"].create_index([("resource_group", 1), ("billing_period", 1)])
await db["azure_cost_details"].create_index([("charge_type", 1)])
await db["azure_cost_details"].create_index([("pre_tax_cost", -1)])
```

---

## Collection 2: `azure_cost_summary`

**Purpose:** Pre-aggregated cost summaries for fast dashboard queries. Rebuilt from `azure_cost_details` after each sync.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "period": str,                  # "YYYY-MM"
    "subscription_id": str,
    "dimension": str,               # "ServiceName", "ResourceGroupName", "Location", "ChargeType"
    "dimension_value": str,         # e.g. "Virtual Machines"
    "total_cost": float,
    "currency": str,
    "usage_quantity": float,
    "unit_of_measure": str,
    "prior_period": str,            # "YYYY-MM" of the previous month
    "prior_period_cost": float,
    "change_amount": float,         # total_cost - prior_period_cost
    "change_pct": float,            # percentage change vs prior period
    "record_count": int,            # number of line items aggregated
    "sync_timestamp": datetime,
}
```

**Upsert Key:**
```python
{"period": record["period"], "subscription_id": record["subscription_id"], "dimension": record["dimension"], "dimension_value": record["dimension_value"]}
```

**Indexes:**
```python
await db["azure_cost_summary"].create_index(
    [("period", 1), ("dimension", 1), ("subscription_id", 1)],
    unique=True
)
await db["azure_cost_summary"].create_index([("period", 1), ("total_cost", -1)])
await db["azure_cost_summary"].create_index([("dimension", 1)])
```

---

## Collection 3: `azure_invoices`

**Purpose:** Invoice history with amounts, status, and download links.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "invoice_id": str,              # e.g. "INV-2026-05-XXXXX"
    "billing_account_id": str,
    "billing_profile_id": str | None,
    "subscription_id": str | None,  # MOSP only
    "billing_period_start": str,    # "YYYY-MM-DD"
    "billing_period_end": str,      # "YYYY-MM-DD"
    "invoice_date": str,            # "YYYY-MM-DD"
    "due_date": str | None,
    "billed_amount": float,
    "amount_due": float,
    "azure_prepayment_applied": float,
    "credit_amount": float,
    "tax_amount": float,
    "billing_currency": str,
    "status": str,                  # "Due", "Paid", "PastDue", "Void"
    "purchase_order_number": str | None,
    "invoice_download_url": str | None,   # SAS URL for PDF (expires)
    "invoice_download_expiry": datetime | None,
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"invoice_id": record["invoice_id"]}`

**Indexes:**
```python
await db["azure_invoices"].create_index([("invoice_id", 1)], unique=True)
await db["azure_invoices"].create_index([("billing_period_start", -1)])
await db["azure_invoices"].create_index([("status", 1)])
```

---

## Collection 4: `azure_budgets`

**Purpose:** Budget definitions with current spend and utilization percentages.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "budget_name": str,
    "budget_id": str,               # Full ARM ID
    "scope": str,                   # Subscription or RG scope URI
    "category": str,                # "Cost"
    "amount": float,                # Budget limit
    "time_grain": str,              # "Monthly", "Quarterly", "Annually", "BillingMonth"
    "time_period_start": str,
    "time_period_end": str,
    "current_spend": float,
    "current_spend_currency": str,
    "forecast_spend": float | None,
    "forecast_spend_currency": str | None,
    "utilization_pct": float,       # (current_spend / amount) * 100
    "forecast_utilization_pct": float | None,
    "notifications": list[dict],    # Array of notification rule objects
    "filter": dict | None,          # Resource/tag/meter filter on the budget
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"budget_id": record["budget_id"]}`

**Indexes:**
```python
await db["azure_budgets"].create_index([("scope", 1)])
await db["azure_budgets"].create_index([("utilization_pct", -1)])
await db["azure_budgets"].create_index([("time_grain", 1)])
```

---

## Collection 5: `azure_cost_alerts`

**Purpose:** Active cost alerts from the Alerts API.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "alert_id": str,                # Full ARM ID
    "alert_name": str,
    "alert_type": str,              # "Budget", "Invoice", "Credit", "Quota"
    "category": str,                # "Cost", "Usage"
    "criteria": str,                # "CostThresholdExceeded"
    "scope": str,
    "description": str,
    "source": str,                  # "Budget", "Preset", "User"
    "status": str,                  # "Active", "Dismissed", "Resolved"
    "creation_time": datetime,
    "close_time": datetime | None,
    "budget_name": str | None,
    "budget_id": str | None,
    "threshold": float | None,      # Budget percentage threshold
    "current_spend": float | None,
    "amount_due": float | None,
    "currency": str | None,
    "triggered_by": str | None,     # Notification rule name that fired
    "time_grain": str | None,
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"alert_id": record["alert_id"]}`

**Indexes:**
```python
await db["azure_cost_alerts"].create_index([("status", 1)])
await db["azure_cost_alerts"].create_index([("creation_time", -1)])
await db["azure_cost_alerts"].create_index([("alert_type", 1)])
```

---

## Collection 6: `azure_advisor_recommendations`

**Purpose:** Azure Advisor recommendations — especially cost optimization ones.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "recommendation_id": str,       # GUID from Azure Advisor
    "recommendation_arm_id": str,   # Full ARM resource ID
    "category": str,                # "Cost", "Security", "Performance", "HighAvailability", "OperationalExcellence"
    "impact": str,                  # "High", "Medium", "Low"
    "impacted_field": str,          # e.g. "Microsoft.Compute/virtualMachines"
    "impacted_value": str,          # e.g. "rh-prod-vm-01"
    "resource_id": str,
    "resource_group": str,
    "subscription_id": str,
    "problem_description": str,     # shortDescription.problem
    "solution_description": str,    # shortDescription.solution
    "extended_properties": dict,    # Full extendedProperties dict
    "estimated_monthly_savings": float | None,
    "estimated_annual_savings": float | None,
    "savings_currency": str | None,
    "current_sku": str | None,
    "recommended_sku": str | None,
    "recommendation_type_id": str,
    "last_updated": datetime,
    "status": str,                  # "Active", "Inactive"
    "suppressed": bool,
    "suppression_ids": list[str],
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"recommendation_id": record["recommendation_id"]}`

**Indexes:**
```python
await db["azure_advisor_recommendations"].create_index([("category", 1), ("status", 1)])
await db["azure_advisor_recommendations"].create_index([("estimated_monthly_savings", -1)])
await db["azure_advisor_recommendations"].create_index([("subscription_id", 1)])
await db["azure_advisor_recommendations"].create_index([("impact", 1)])
await db["azure_advisor_recommendations"].create_index([("last_updated", -1)])
```

---

## Collection 7: `azure_reservation_details`

**Purpose:** Daily reservation utilization records.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "reservation_id": str,
    "reservation_order_id": str,
    "sku_name": str,                # e.g. "Standard_D4s_v3"
    "kind": str,                    # e.g. "Microsoft.Compute"
    "instance_flexibility_group": str | None,
    "instance_flexibility_ratio": float | None,
    "usage_date": str,              # "YYYY-MM-DD"
    "billing_period": str,          # "YYYY-MM"
    "utilized_hours": float,
    "reserved_hours": float,
    "total_reserved_quantity": float,
    "utilization_pct": float,       # (utilized_hours / reserved_hours) * 100
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"reservation_id": r["reservation_id"], "usage_date": r["usage_date"]}`

**Indexes:**
```python
await db["azure_reservation_details"].create_index([("billing_period", 1)])
await db["azure_reservation_details"].create_index([("reservation_id", 1), ("usage_date", 1)], unique=True)
await db["azure_reservation_details"].create_index([("utilization_pct", 1)])
```

---

## Collection 8: `azure_reservation_recommendations`

**Purpose:** AI-generated reservation purchase recommendations.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "subscription_id": str,
    "sku_name": str,
    "resource_type": str,           # e.g. "virtualMachines"
    "scope": str,                   # "Single" or "Shared"
    "term": str,                    # "P1Y" or "P3Y"
    "look_back_period": str,        # "Last7Days", "Last30Days", "Last60Days"
    "location": str,                # Azure region
    "recommended_quantity": float,
    "recommended_quantity_normalized": float,
    "instance_flexibility_group": str | None,
    "instance_flexibility_ratio": float | None,
    "meter_id": str | None,
    "first_usage_date": str | None,
    "total_cost_with_no_ri": float,     # Current pay-as-you-go cost
    "total_cost_with_ri": float,        # Cost with recommended RIs
    "net_savings": float,               # Monthly savings
    "currency": str,
    "sku_properties": list[dict],       # SKU-specific properties
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"subscription_id": r["subscription_id"], "sku_name": r["sku_name"], "term": r["term"], "scope": r["scope"], "location": r["location"]}`

**Indexes:**
```python
await db["azure_reservation_recommendations"].create_index([("net_savings", -1)])
await db["azure_reservation_recommendations"].create_index([("term", 1)])
await db["azure_reservation_recommendations"].create_index([("subscription_id", 1)])
```

---

## Collection 9: `azure_resource_inventory`

**Purpose:** Complete inventory of all Azure resources with metadata — used for AI context.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "resource_id": str,             # Full ARM resource ID
    "resource_name": str,
    "resource_type": str,           # e.g. "microsoft.compute/virtualmachines"
    "subscription_id": str,
    "resource_group": str,
    "location": str,
    "sku": dict | None,             # {"name": "P1v3", "tier": "PremiumV3"}
    "kind": str | None,
    "tags": dict,
    "provisioning_state": str | None,
    "power_state": str | None,      # For VMs: "PowerState/running", "PowerState/deallocated"
    "properties": dict,             # Subset of resource properties (not all — too large)
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"resource_id": record["resource_id"]}`

**Indexes:**
```python
await db["azure_resource_inventory"].create_index([("resource_id", 1)], unique=True)
await db["azure_resource_inventory"].create_index([("resource_type", 1)])
await db["azure_resource_inventory"].create_index([("resource_group", 1)])
await db["azure_resource_inventory"].create_index([("subscription_id", 1)])
```

---

## Collection 10: `azure_retail_prices`

**Purpose:** Public retail pricing for comparison against contracted prices.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "meter_id": str,
    "sku_id": str,
    "product_id": str,
    "meter_name": str,
    "product_name": str,
    "sku_name": str,
    "service_name": str,
    "service_family": str,
    "service_id": str,
    "arm_region_name": str,
    "location": str,
    "retail_price": float,
    "unit_price": float,
    "currency_code": str,
    "unit_of_measure": str,
    "type": str,                    # "Consumption" or "Reservation"
    "tier_minimum_units": float,
    "is_primary_meter_region": bool,
    "arm_sku_name": str | None,
    "effective_start_date": str,    # "YYYY-MM-DDT00:00:00Z"
    "effective_end_date": str | None,
    "sync_timestamp": datetime,
}
```

**Upsert Key:** `{"meter_id": r["meter_id"], "arm_region_name": r["arm_region_name"], "type": r["type"]}`

**Indexes:**
```python
await db["azure_retail_prices"].create_index([("meter_id", 1), ("arm_region_name", 1)], unique=False)
await db["azure_retail_prices"].create_index([("service_name", 1)])
await db["azure_retail_prices"].create_index([("sku_name", 1)])
```

---

## Collection 11: `azure_billing_sync_log`

**Purpose:** Audit log of all sync job executions.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "sync_type": str,               # "cost_details_daily", "advisor", "budgets", "invoices", "reservations", "resource_inventory", "retail_prices", "full_backfill", "vectorize"
    "status": str,                  # "running", "completed", "failed"
    "billing_period": str | None,   # "YYYY-MM" if applicable
    "started_at": datetime,
    "completed_at": datetime | None,
    "records_synced": int,
    "records_skipped": int,
    "error_message": str | None,
    "duration_seconds": float | None,
    "triggered_by": str,            # "scheduler", "manual_api", "startup_backfill"
}
```

**No upsert — always insert a new log entry per run.**

**Indexes:**
```python
await db["azure_billing_sync_log"].create_index([("sync_type", 1), ("started_at", -1)])
await db["azure_billing_sync_log"].create_index([("status", 1)])
await db["azure_billing_sync_log"].create_index([("started_at", -1)])
```

---

## Collection 12: `azure_billing_vectors`

**Purpose:** Vectorized billing insight documents for MongoDB Atlas Vector Search and AI queries.

**Document Schema:**
```python
{
    "_id": ObjectId,
    "document_type": str,           # See Section 12.1 for all type values
    "text": str,                    # The full natural language text that was embedded
    "embedding": list[float],       # 1536-dimensional vector from text-embedding-3-small
    "metadata": {
        "period": str | None,               # "YYYY-MM"
        "subscription_id": str | None,
        "dimension": str | None,            # e.g. "ServiceName"
        "dimension_value": str | None,      # e.g. "Virtual Machines"
        "total_cost": float | None,
        "currency": str | None,
        "recommendation_id": str | None,    # For advisor documents
        "estimated_monthly_savings": float | None,
        "source_collection": str,           # Which MongoDB collection this came from
        "source_ids": list[str],            # ObjectId strings of source documents
    },
    "model": str,                   # "text-embedding-3-small"
    "dimensions": int,              # 1536
    "created_at": datetime,
    "last_updated": datetime,
}
```

**Upsert Key:** `{"document_type": d["document_type"], "metadata.period": d["metadata"].get("period"), "metadata.dimension_value": d["metadata"].get("dimension_value")}`

**Indexes (Motor driver `create_index`):**
```python
await db["azure_billing_vectors"].create_index([("document_type", 1)])
await db["azure_billing_vectors"].create_index([("metadata.period", 1)])
await db["azure_billing_vectors"].create_index([("created_at", -1)])
```

**Atlas Vector Search Index (must be created in MongoDB Atlas UI or Admin API):**

Index name: `billing_vector_index`  
Collection: `azure_billing_vectors`

JSON definition for the Atlas Search Index:
```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "document_type"
    },
    {
      "type": "filter",
      "path": "metadata.period"
    },
    {
      "type": "filter",
      "path": "metadata.subscription_id"
    }
  ]
}
```

**How to create this index in Atlas UI:**
1. In MongoDB Atlas, navigate to your cluster
2. Select the **Search** tab (or **Atlas Search** in the cluster view)
3. Click **Create Search Index**
4. Select **Atlas Vector Search** → **JSON Editor**
5. Select database `recoveryhub_dashboard`, collection `azure_billing_vectors`
6. Paste the JSON definition above
7. Name the index `billing_vector_index`
8. Click **Create**

The index takes a few minutes to build. The `$vectorSearch` aggregation stage will fail with an error if this index does not exist. The application startup check in `init_indexes()` must warn if the index is missing — see Supporting Doc 05 for the implementation.

---

## MongoDB Atlas Tier Requirement

MongoDB Atlas Vector Search requires **M10 or higher** cluster tier. The free M0 tier and shared M2/M5 tiers do not support vector search indexes. Verify your Atlas cluster tier before deploying Phase 6.

---

## `init_indexes()` Extension Pattern

The billing index creation must be appended to the existing `init_indexes()` method in `database.py`. Follow the existing pattern:

```python
async def init_indexes(self) -> None:
    """Pre-configures collection indexes on startup to ensure high lookup speeds."""
    if self.db is None:
        raise RuntimeError("Database connection not established. Call connect() first.")

    try:
        # --- Existing indexes (do not modify) ---
        await self.db["dashboards"].create_index([("created_by", 1)])
        await self.db["users"].create_index([("email", 1)], unique=True)

        # --- Billing collection indexes (new additions) ---
        await self.db["azure_cost_details"].create_index([("billing_period", 1), ("subscription_id", 1)])
        # ... (all indexes listed above)

        # --- Vector index existence check ---
        await self._check_vector_index()

        logger.info("MongoDB index initialization completed.")
    except Exception as e:
        logger.error(f"Failed to create indexes in MongoDB: {e}")
        raise e

async def _check_vector_index(self) -> None:
    """Checks for the Atlas Vector Search index and logs a warning if missing."""
    try:
        indexes = await self.db["azure_billing_vectors"].list_search_indexes().to_list(length=10)
        vector_index_exists = any(idx.get("name") == "billing_vector_index" for idx in indexes)
        if not vector_index_exists:
            logger.warning(
                "Atlas Vector Search index 'billing_vector_index' is missing on "
                "'azure_billing_vectors' collection. AI billing queries will not work until "
                "this index is created. See docs/supporting/04-mongodb-schema-design.md "
                "Section 12 for the index JSON definition and creation instructions."
            )
    except Exception as e:
        logger.warning(f"Could not verify Atlas Vector Search index existence: {e}")
```

Note: `list_search_indexes()` requires MongoDB Atlas and Motor 3.x — it is not available on standalone MongoDB instances. The `try/except` on `_check_vector_index` ensures local Docker MongoDB (used in development) does not fail on startup.
