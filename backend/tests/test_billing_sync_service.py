"""Tests for billing.sync_service orchestration — Azure clients mocked, mongomock DB."""
import pytest

from billing import sync_service


def _csv_row(**overrides):
    base = {
        "SubscriptionId": "sub1",
        "SubscriptionName": "Prod",
        "Date": "2026-05-15",
        "ResourceId": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
        "ResourceGroupName": "rg1",
        "MeterId": "meter1",
        "MeterCategory": "Virtual Machines",
        "ServiceFamily": "Compute",
        "ChargeType": "Usage",
        "Cost": "12.50",
        "Quantity": "3",
        "BillingCurrency": "USD",
        "Tags": '{"env":"prod"}',
        "IsAzureCreditEligible": "True",
    }
    base.update(overrides)
    return base


def test_clean_cost_row_maps_fields():
    record = sync_service._clean_cost_row(_csv_row(), "2026-05")
    assert record["pre_tax_cost"] == 12.50
    assert record["quantity"] == 3.0
    assert record["resource_name"] == "vm1"
    assert record["service_name"] == "Virtual Machines"
    assert record["tags"] == {"env": "prod"}
    assert record["is_azure_credit_eligible"] is True
    assert record["data_source"] == "cost_details_api"


def test_period_dates():
    start, end = sync_service._period_dates("2026-02")
    assert start == "2026-02-01"
    assert end == "2026-02-28"


def test_recent_periods_ordering():
    periods = sync_service._recent_periods(3)
    assert len(periods) == 3
    assert periods == sorted(periods)  # oldest first


@pytest.mark.asyncio
async def test_sync_cost_details_upserts_and_logs(monkeypatch, mock_mongo_db):
    async def fake_report(scope, start, end, metric="ActualCost"):
        return [_csv_row(), _csv_row(MeterId="meter2", Cost="5.00")]
    monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", fake_report)

    count = await sync_service.sync_cost_details(mock_mongo_db, "2026-05", "manual_api")

    # 2 rows x 2 metrics (ActualCost + AmortizedCost) = 4 upsert ops
    assert count == 4
    details = await mock_mongo_db["azure_cost_details"].find({}).to_list(length=None)
    assert len(details) == 2  # deduped by upsert key (meter1, meter2)

    summary = await mock_mongo_db["azure_cost_summary"].find({"dimension": "ServiceName"}).to_list(length=None)
    assert any(s["dimension_value"] == "Virtual Machines" for s in summary)

    logs = await mock_mongo_db["azure_billing_sync_log"].find({"sync_type": "cost_details_daily"}).to_list(length=None)
    assert logs[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_sync_cost_details_records_failure(monkeypatch, mock_mongo_db):
    async def boom(scope, start, end, metric="ActualCost"):
        raise RuntimeError("api down")
    monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", boom)

    with pytest.raises(RuntimeError):
        await sync_service.sync_cost_details(mock_mongo_db, "2026-05", "manual_api")

    logs = await mock_mongo_db["azure_billing_sync_log"].find({"sync_type": "cost_details_daily"}).to_list(length=None)
    assert logs[0]["status"] == "failed"
    assert "api down" in logs[0]["error_message"]


@pytest.mark.asyncio
async def test_sync_advisor_maps_and_deactivates(monkeypatch, mock_mongo_db):
    async def fake_all(sub_id):
        return [{
            "name": "rec-1",
            "id": "/arm/rec-1",
            "properties": {
                "category": "Cost",
                "impact": "High",
                "impactedField": "Microsoft.Compute/virtualMachines",
                "impactedValue": "vm1",
                "shortDescription": {"problem": "Idle VM", "solution": "Resize"},
                "extendedProperties": {"savingsAmount": "100", "savingsCurrency": "USD"},
                "resourceMetadata": {"resourceId": "/subscriptions/sub1/resourceGroups/rg1/x"},
            },
        }]
    monkeypatch.setattr(sync_service.advisor, "get_all_recommendations", fake_all)

    count = await sync_service.sync_advisor_recommendations(mock_mongo_db, "manual_api")
    assert count == 1
    rec = await mock_mongo_db["azure_advisor_recommendations"].find_one({"recommendation_id": "rec-1"})
    assert rec["estimated_monthly_savings"] == 100.0
    assert rec["estimated_annual_savings"] == 1200.0
    assert rec["resource_group"] == "rg1"


@pytest.mark.asyncio
async def test_sync_budgets_computes_utilization(monkeypatch, mock_mongo_db):
    async def fake_budgets(scope):
        return [{
            "name": "Prod-Monthly",
            "id": "/arm/budget-1",
            "properties": {
                "category": "Cost",
                "amount": 1000.0,
                "timeGrain": "Monthly",
                "timePeriod": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
                "currentSpend": {"amount": 800.0, "unit": "USD"},
                "notifications": {"a": {"enabled": True}},
            },
        }]
    monkeypatch.setattr(sync_service.cost_management, "get_budgets", fake_budgets)

    count = await sync_service.sync_budgets(mock_mongo_db, "manual_api")
    assert count == 1
    budget = await mock_mongo_db["azure_budgets"].find_one({"budget_id": "/arm/budget-1"})
    assert budget["utilization_pct"] == 80.0
    assert isinstance(budget["notifications"], list)


@pytest.mark.asyncio
async def test_run_full_backfill_skips_when_populated(monkeypatch, mock_mongo_db):
    await mock_mongo_db["azure_cost_details"].insert_one({"x": 1})
    result = await sync_service.run_full_backfill(mock_mongo_db, 3, "startup_backfill")
    assert result == {"skipped": True}


def test_clean_cost_row_returns_none_for_missing_date():
    """Rows missing the Date field are rejected (return None)."""
    row = _csv_row()
    row["Date"] = ""
    result = sync_service._clean_cost_row(row, "2026-05")
    assert result is None


def test_clean_cost_row_returns_none_for_missing_meter_id():
    """Rows missing the MeterId field are rejected (return None)."""
    row = _csv_row()
    row["MeterId"] = ""
    result = sync_service._clean_cost_row(row, "2026-05")
    assert result is None


def test_clean_cost_row_allows_empty_resource_id():
    """ResourceId is optional (some charge types don't have one) — should not reject."""
    row = _csv_row()
    row["ResourceId"] = ""
    result = sync_service._clean_cost_row(row, "2026-05")
    assert result is not None
    assert result["resource_id"] == ""


def test_map_advisor_returns_none_for_missing_category():
    """Advisor records missing category are rejected (return None)."""
    rec = {
        "name": "rec-1",
        "id": "/arm/rec-1",
        "properties": {
            "category": "",  # Missing
            "impact": "High",
            "impactedField": "Microsoft.Compute/virtualMachines",
            "impactedValue": "vm1",
            "shortDescription": {"problem": "Idle VM", "solution": "Resize"},
            "extendedProperties": {"savingsAmount": "100", "savingsCurrency": "USD"},
            "resourceMetadata": {"resourceId": "/subscriptions/sub1/resourceGroups/rg1/x"},
        },
    }
    result = sync_service._map_advisor(rec)
    assert result is None


def test_map_advisor_returns_none_for_missing_id():
    """Advisor records missing recommendation_id (name) are rejected."""
    rec = {
        "name": "",
        "id": "/arm/rec-1",
        "properties": {
            "category": "Cost",
            "impact": "High",
        },
    }
    result = sync_service._map_advisor(rec)
    assert result is None


@pytest.mark.asyncio
async def test_sync_cost_details_skips_invalid_rows(monkeypatch, mock_mongo_db):
    """Invalid rows (missing Date/MeterId) are skipped during sync, valid ones are upserted."""
    async def fake_report(scope, start, end, metric="ActualCost"):
        return [
            _csv_row(),                              # valid
            _csv_row(MeterId="meter2", Cost="5.00"),  # valid
            _csv_row(Date=""),                        # invalid (no date)
            _csv_row(MeterId=""),                     # invalid (no meter_id)
        ]
    monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", fake_report)

    count = await sync_service.sync_cost_details(mock_mongo_db, "2026-05", "manual_api")
    # 2 valid rows x 2 metrics = 4 upserts (invalid rows skipped)
    assert count == 4


# ---------------------------------------------------------------------------
# Concurrency locks (S1) — overlapping sync runs of the same type must skip
# ---------------------------------------------------------------------------

class TestSyncLocks:
    """Each sync_type has an asyncio.Lock; a second call while the first is
    still inside the `async with lock:` block must return 0 immediately
    rather than queueing or overlapping."""

    @pytest.mark.asyncio
    async def test_overlapping_same_sync_type_skips(self, monkeypatch, mock_mongo_db):
        """Hold the cost_details lock manually, then call sync_cost_details —
        it must observe the lock is held and skip (return 0)."""
        # Clear any stale locks from prior tests
        sync_service._sync_locks.clear()
        lock = sync_service._get_sync_lock("cost_details_2026-05")

        called = {"count": 0}

        async def fake_report(scope, start, end, metric="ActualCost"):
            called["count"] += 1
            return []

        monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", fake_report)

        async with lock:
            # Lock is now held — a concurrent sync must skip
            result = await sync_service.sync_cost_details(mock_mongo_db, "2026-05", "manual_api")
        assert result == 0
        assert called["count"] == 0  # The Azure API was never called

    @pytest.mark.asyncio
    async def test_different_sync_types_run_independently(self, monkeypatch, mock_mongo_db):
        """Different sync_types have separate locks — holding one must not
        block another."""
        sync_service._sync_locks.clear()
        cost_lock = sync_service._get_sync_lock("cost_details_2026-05")

        async def fake_budgets(scope):
            return []
        monkeypatch.setattr(sync_service.cost_management, "get_budgets", fake_budgets)

        async with cost_lock:
            # budgets uses a different lock, so it should proceed normally
            result = await sync_service.sync_budgets(mock_mongo_db, "manual_api")
        assert result == 0  # 0 budgets returned, but it ran (didn't skip)

    @pytest.mark.asyncio
    async def test_lock_released_after_sync_completes(self, monkeypatch, mock_mongo_db):
        """After a sync finishes (even on error), the lock must be released
        so the next run can proceed."""
        sync_service._sync_locks.clear()

        async def boom(scope, start, end, metric="ActualCost"):
            raise RuntimeError("fail")
        monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", boom)

        with pytest.raises(RuntimeError):
            await sync_service.sync_cost_details(mock_mongo_db, "2026-05", "manual_api")

        lock = sync_service._get_sync_lock("cost_details_2026-05")
        assert lock.locked() is False


# ---------------------------------------------------------------------------
# sync_alerts
# ---------------------------------------------------------------------------

def _alert_raw(**overrides):
    base = {
        "id": "/alerts/alert-1",
        "name": "Budget Alert 80%",
        "properties": {
            "definition": {"type": "Budget", "category": "Cost", "criteria": "Cost"},
            "description": "Threshold exceeded",
            "source": "Cost Management",
            "status": "Active",
            "creationTime": "2026-05-15T10:00:00Z",
            "closeTime": None,
            "details": {
                "budgetName": "Prod-Monthly",
                "budgetId": "/arm/budget-1",
                "threshold": "80",
                "currentSpend": "800",
                "unit": "USD",
                "triggeredBy": "Threshold",
                "timeGrainType": "Monthly",
            },
        },
    }
    _deep_merge(base, overrides)
    return base


def _deep_merge(base, overrides):
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def test_map_alert_maps_fields():
    alert = sync_service._map_alert(_alert_raw())
    assert alert["alert_id"] == "/alerts/alert-1"
    assert alert["alert_name"] == "Budget Alert 80%"
    assert alert["alert_type"] == "Budget"
    assert alert["threshold"] == 80.0
    assert alert["current_spend"] == 800.0
    assert alert["currency"] == "USD"
    assert alert["creation_time"] is not None
    assert alert["close_time"] is None


def test_parse_dt_handles_various_formats():
    assert sync_service._parse_dt(None) is None
    assert sync_service._parse_dt("") is None
    # ISO with Z suffix
    dt = sync_service._parse_dt("2026-05-15T10:00:00Z")
    assert dt is not None and dt.year == 2026
    # Bad string
    assert sync_service._parse_dt("not-a-date") is None
    # Already a datetime
    from datetime import datetime
    now = datetime.now()
    assert sync_service._parse_dt(now) is now


@pytest.mark.asyncio
async def test_sync_alerts_upserts_and_logs(monkeypatch, mock_mongo_db):
    async def fake_alerts(scope):
        return [_alert_raw(), _alert_raw(id="/alerts/alert-2", name="Alert 2")]
    monkeypatch.setattr(sync_service.cost_management, "get_alerts", fake_alerts)

    count = await sync_service.sync_alerts(mock_mongo_db, "manual_api")
    assert count == 2
    docs = await mock_mongo_db["azure_cost_alerts"].find({}).to_list(length=None)
    assert len(docs) == 2
    logs = await mock_mongo_db["azure_billing_sync_log"].find({"sync_type": "alerts"}).to_list(length=None)
    assert logs[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_sync_alerts_skips_empty_ids(monkeypatch, mock_mongo_db):
    async def fake_alerts(scope):
        return [_alert_raw(id=""), _alert_raw(id="/alerts/real")]
    monkeypatch.setattr(sync_service.cost_management, "get_alerts", fake_alerts)

    count = await sync_service.sync_alerts(mock_mongo_db, "manual_api")
    assert count == 2  # returns len(alerts), but only 1 is upserted
    docs = await mock_mongo_db["azure_cost_alerts"].find({}).to_list(length=None)
    assert len(docs) == 1


# ---------------------------------------------------------------------------
# sync_invoices
# ---------------------------------------------------------------------------

def _invoice_raw(**overrides):
    base = {
        "name": "202605-1",
        "id": "/invoices/202605-1",
        "properties": {
            "invoicePeriodStartDate": "2026-05-01",
            "invoicePeriodEndDate": "2026-05-31",
            "invoiceDate": "2026-06-05",
            "dueDate": "2026-06-15",
            "totalAmount": {"value": "1500.00", "currency": "USD"},
            "amountDue": {"value": "500.00", "currency": "USD"},
            "billingCurrency": "USD",
            "status": "Paid",
            "billingProfileId": "/billingProfiles/1",
        },
    }
    _deep_merge(base, overrides)
    return base


def test_map_invoice_maps_fields():
    inv = sync_service._map_invoice(_invoice_raw())
    assert inv["invoice_id"] == "202605-1"
    assert inv["billed_amount"] == 1500.0
    assert inv["amount_due"] == 500.0
    assert inv["status"] == "Paid"
    assert inv["billing_period_start"] == "2026-05-01"


def test_map_invoice_falls_back_to_id_when_name_empty():
    inv = sync_service._map_invoice(_invoice_raw(name=""))
    assert inv["invoice_id"] == "/invoices/202605-1"


def test_map_invoice_handles_flat_amount_fields():
    """Some invoice payloads use flat `billedAmount`/`amountDue` instead of nested dict."""
    raw = _invoice_raw()
    raw["properties"]["totalAmount"] = "999.00"  # not a dict → falls through
    raw["properties"]["billedAmount"] = "999.00"
    raw["properties"]["amountDue"] = "100.00"
    inv = sync_service._map_invoice(raw)
    assert inv["billed_amount"] == 999.0
    assert inv["amount_due"] == 100.0


@pytest.mark.asyncio
async def test_sync_invoices_upserts_and_logs(monkeypatch, mock_mongo_db):
    async def fake_invoices(account_id, account_type):
        return [_invoice_raw(), _invoice_raw(name="202604-1")]
    monkeypatch.setattr(sync_service.billing_accounts, "get_invoices", fake_invoices)

    count = await sync_service.sync_invoices(mock_mongo_db, "manual_api")
    assert count == 2
    docs = await mock_mongo_db["azure_invoices"].find({}).to_list(length=None)
    assert len(docs) == 2


# ---------------------------------------------------------------------------
# sync_reservations
# ---------------------------------------------------------------------------

def _reservation_detail_raw(**overrides):
    base = {
        "properties": {
            "reservationId": "res-1",
            "reservationOrderId": "order-1",
            "skuName": "Standard_DS2",
            "usageDate": "2026-05-15T00:00:00Z",
            "reservedHours": 744.0,
            "usedHours": 600.0,
            "kind": "Standard",
        }
    }
    _deep_merge(base, overrides)
    return base


def test_map_reservation_detail_maps_fields():
    detail = sync_service._map_reservation_detail(_reservation_detail_raw())
    assert detail["reservation_id"] == "res-1"
    assert detail["reserved_hours"] == 744.0
    assert detail["utilized_hours"] == 600.0
    assert detail["utilization_pct"] == round(600.0 / 744.0 * 100, 2)
    assert detail["usage_date"] == "2026-05-15"
    assert detail["billing_period"] == "2026-05"


def test_map_reservation_detail_zero_reserved():
    detail = sync_service._map_reservation_detail(_reservation_detail_raw(
        properties={"reservedHours": 0, "usedHours": 0, "reservationId": "r", "usageDate": "2026-05-15"}
    ))
    assert detail["utilization_pct"] == 0.0


def _reservation_rec_raw(**overrides):
    base = {
        "properties": {
            "skuName": "Standard_DS2_v2",
            "resourceType": "virtualMachines",
            "scope": "Single",
            "term": "P1Y",
            "lookBackPeriod": "Last30Days",
            "location": "eastus",
            "recommendedQuantity": 3,
            "costWithNoReservedInstances": 1000.0,
            "totalCostWithReservedInstances": 700.0,
            "netSavings": 300.0,
            "currencyCode": "USD",
        }
    }
    _deep_merge(base, overrides)
    return base


def test_map_reservation_recommendation_maps_fields():
    rec = sync_service._map_reservation_recommendation(_reservation_rec_raw())
    assert rec["sku_name"] == "Standard_DS2_v2"
    assert rec["term"] == "P1Y"
    assert rec["net_savings"] == 300.0
    assert rec["currency"] == "USD"


@pytest.mark.asyncio
async def test_sync_reservations_upserts_details_and_recs(monkeypatch, mock_mongo_db):
    async def fake_details(scope, start, end):
        return [_reservation_detail_raw()]
    async def fake_recs(scope, term, look_back):
        # Return a rec whose term matches the requested term so each
        # (term, look_back) combo produces a distinct upsert key.
        return [_reservation_rec_raw(properties={"term": term, "lookBackPeriod": look_back})]
    monkeypatch.setattr(sync_service.consumption, "get_reservation_details", fake_details)
    monkeypatch.setattr(sync_service.consumption, "get_reservation_recommendations", fake_recs)

    count = await sync_service.sync_reservations(mock_mongo_db, "manual_api")
    # 1 detail + 2 terms x 2 look_back periods x 1 rec = 1 + 4 = 5
    assert count == 5
    details = await mock_mongo_db["azure_reservation_details"].find({}).to_list(length=None)
    assert len(details) == 1
    recs = await mock_mongo_db["azure_reservation_recommendations"].find({}).to_list(length=None)
    # 2 unique docs (P1Y and P3Y) — look_back is not part of the upsert key,
    # so the 2 look_back variants for each term collapse into 1 doc each.
    assert len(recs) == 2


@pytest.mark.asyncio
async def test_sync_reservations_skips_empty_ids(monkeypatch, mock_mongo_db):
    async def fake_details(scope, start, end):
        return [_reservation_detail_raw(properties={"reservationId": "", "usageDate": "2026-05-15"})]
    async def fake_recs(scope, term, look_back):
        return []
    monkeypatch.setattr(sync_service.consumption, "get_reservation_details", fake_details)
    monkeypatch.setattr(sync_service.consumption, "get_reservation_recommendations", fake_recs)

    count = await sync_service.sync_reservations(mock_mongo_db, "manual_api")
    assert count == 0  # detail skipped (empty reservation_id), no recs


# ---------------------------------------------------------------------------
# sync_resource_inventory
# ---------------------------------------------------------------------------

def _resource_raw(**overrides):
    base = {
        "id": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
        "name": "vm1",
        "type": "Microsoft.Compute/virtualMachines",
        "subscriptionId": "sub1",
        "resourceGroup": "rg1",
        "location": "eastus",
        "sku": {"name": "Standard_DS2_v2"},
        "kind": "virtualMachine",
        "tags": {"env": "prod"},
        "properties": {"provisioningState": "Succeeded"},
    }
    _deep_merge(base, overrides)
    return base


def test_map_resource_maps_fields():
    res = sync_service._map_resource(_resource_raw())
    assert res["resource_id"] == "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1"
    assert res["resource_name"] == "vm1"
    assert res["resource_group"] == "rg1"
    assert res["provisioning_state"] == "Succeeded"
    assert res["tags"] == {"env": "prod"}


def test_map_resource_handles_missing_properties():
    res = sync_service._map_resource(_resource_raw(properties=None))
    assert res["provisioning_state"] is None


@pytest.mark.asyncio
async def test_sync_resource_inventory_upserts_and_applies_power_state(monkeypatch, mock_mongo_db):
    async def fake_query(subs, kql):
        if kql == sync_service.resource_graph.KQL_DEALLOCATED_VMS:
            return [{"id": "/subscriptions/sub1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1",
                     "powerState": "VM deallocated"}]
        return [_resource_raw()]
    monkeypatch.setattr(sync_service.resource_graph, "query_resources", fake_query)

    count = await sync_service.sync_resource_inventory(mock_mongo_db, "manual_api")
    assert count == 1
    doc = await mock_mongo_db["azure_resource_inventory"].find_one({"resource_name": "vm1"})
    assert doc["power_state"] == "VM deallocated"


# ---------------------------------------------------------------------------
# sync_retail_prices
# ---------------------------------------------------------------------------

def _retail_price_raw(**overrides):
    base = {
        "meterId": "meter-1",
        "skuId": "sku-1",
        "productId": "prod-1",
        "meterName": "D2 v2",
        "productName": "Virtual Machines Dv2 Series",
        "skuName": "D2_v2",
        "serviceName": "Virtual Machines",
        "serviceFamily": "Compute",
        "armRegionName": "eastus",
        "retailPrice": 0.5,
        "unitPrice": 0.5,
        "currencyCode": "USD",
        "unitOfMeasure": "1 Hour",
        "type": "Consumption",
        "isPrimaryMeterRegion": True,
    }
    _deep_merge(base, overrides)
    return base


def test_map_retail_price_maps_fields():
    price = sync_service._map_retail_price(_retail_price_raw())
    assert price["meter_id"] == "meter-1"
    assert price["retail_price"] == 0.5
    assert price["is_primary_meter_region"] is True
    assert price["currency_code"] == "USD"


@pytest.mark.asyncio
async def test_sync_retail_prices_upserts_and_logs(monkeypatch, mock_mongo_db):
    async def fake_prices():
        return [_retail_price_raw(), _retail_price_raw(meterId="meter-2")]
    monkeypatch.setattr(sync_service.retail_prices, "sync_common_service_prices", fake_prices)

    count = await sync_service.sync_retail_prices(mock_mongo_db, "manual_api")
    assert count == 2
    docs = await mock_mongo_db["azure_retail_prices"].find({}).to_list(length=None)
    assert len(docs) == 2


@pytest.mark.asyncio
async def test_sync_retail_prices_skips_empty_meter_id(monkeypatch, mock_mongo_db):
    async def fake_prices():
        return [_retail_price_raw(meterId=""), _retail_price_raw()]
    monkeypatch.setattr(sync_service.retail_prices, "sync_common_service_prices", fake_prices)

    count = await sync_service.sync_retail_prices(mock_mongo_db, "manual_api")
    assert count == 2  # returns len(prices), but only 1 upserted
    docs = await mock_mongo_db["azure_retail_prices"].find({}).to_list(length=None)
    assert len(docs) == 1


# ---------------------------------------------------------------------------
# run_daily_sync — composite
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_daily_sync_calls_all_sub_syncs(monkeypatch, mock_mongo_db):
    """run_daily_sync should call cost_details (current + prev), budgets, and alerts."""
    sync_service._sync_locks.clear()
    calls = {"cost_details": 0, "budgets": 0, "alerts": 0}

    async def fake_report(scope, start, end, metric="ActualCost"):
        calls["cost_details"] += 1
        return []
    async def fake_budgets(scope):
        calls["budgets"] += 1
        return []
    async def fake_alerts(scope):
        calls["alerts"] += 1
        return []

    monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", fake_report)
    monkeypatch.setattr(sync_service.cost_management, "get_budgets", fake_budgets)
    monkeypatch.setattr(sync_service.cost_management, "get_alerts", fake_alerts)

    summary = await sync_service.run_daily_sync(mock_mongo_db)
    assert "cost_details_current" in summary
    assert "cost_details_previous" in summary
    assert "budgets" in summary
    assert "alerts" in summary
    # cost_details called for 2 periods x 2 metrics = 4
    assert calls["cost_details"] == 4
    assert calls["budgets"] == 1
    assert calls["alerts"] == 1


# ---------------------------------------------------------------------------
# Failure logging for remaining sync types
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_alerts_records_failure(monkeypatch, mock_mongo_db):
    async def boom(scope):
        raise RuntimeError("alerts API down")
    monkeypatch.setattr(sync_service.cost_management, "get_alerts", boom)

    with pytest.raises(RuntimeError):
        await sync_service.sync_alerts(mock_mongo_db, "manual_api")
    logs = await mock_mongo_db["azure_billing_sync_log"].find({"sync_type": "alerts"}).to_list(length=None)
    assert logs[0]["status"] == "failed"
    assert "alerts API down" in logs[0]["error_message"]


@pytest.mark.asyncio
async def test_sync_invoices_records_failure(monkeypatch, mock_mongo_db):
    async def boom(account_id, account_type):
        raise RuntimeError("invoices API down")
    monkeypatch.setattr(sync_service.billing_accounts, "get_invoices", boom)

    with pytest.raises(RuntimeError):
        await sync_service.sync_invoices(mock_mongo_db, "manual_api")
    logs = await mock_mongo_db["azure_billing_sync_log"].find({"sync_type": "invoices"}).to_list(length=None)
    assert logs[0]["status"] == "failed"
