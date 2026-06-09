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
