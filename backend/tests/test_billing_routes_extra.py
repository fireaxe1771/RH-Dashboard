"""Tests for billing_routes endpoints not covered by test_billing_routes.py."""
import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer valid-mock-token"}


def test_cost_trend_returns_rows(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_summary"]._col.insert_many([
        {"period": "2026-01", "dimension": "ServiceName", "dimension_value": "VMs", "total_cost": 100.0, "currency": "USD"},
        {"period": "2026-02", "dimension": "ServiceName", "dimension_value": "VMs", "total_cost": 120.0, "currency": "USD"},
        {"period": "2026-03", "dimension": "ServiceName", "dimension_value": "Storage", "total_cost": 30.0, "currency": "USD"},
    ])
    resp = test_client.get("/api/billing/cost/trend?dimension=ServiceName", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    # Should be sorted by period ascending
    assert body[0]["period"] == "2026-01"


def test_cost_trend_filters_by_dimension_value(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_summary"]._col.insert_many([
        {"period": "2026-01", "dimension": "ServiceName", "dimension_value": "VMs", "total_cost": 100.0, "currency": "USD"},
        {"period": "2026-01", "dimension": "ServiceName", "dimension_value": "Storage", "total_cost": 30.0, "currency": "USD"},
    ])
    resp = test_client.get("/api/billing/cost/trend?dimension=ServiceName&dimension_value=VMs", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["dimension_value"] == "VMs"


def test_cost_by_tag_buckets_by_tag_value(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_details"]._col.insert_many([
        {"billing_period": "2026-05", "tags": {"env": "prod"}, "pre_tax_cost": 100.0},
        {"billing_period": "2026-05", "tags": {"env": "prod"}, "pre_tax_cost": 50.0},
        {"billing_period": "2026-05", "tags": {"env": "dev"}, "pre_tax_cost": 20.0},
        {"billing_period": "2026-05", "tags": None, "pre_tax_cost": 10.0},
    ])
    resp = test_client.get("/api/billing/cost/by-tag?period=2026-05&tag_key=env", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    # Should have prod, dev, untagged buckets sorted by cost desc
    assert len(body) == 3
    assert body[0]["tag_value"] == "prod"
    assert body[0]["total_cost"] == 150.0
    assert body[1]["tag_value"] == "dev"
    assert body[2]["tag_value"] == "untagged"


def test_cost_daily_buckets_by_date(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_details"]._col.insert_many([
        {"date": "2026-05-01", "service_name": "VMs", "pre_tax_cost": 100.0},
        {"date": "2026-05-01", "service_name": "Storage", "pre_tax_cost": 20.0},
        {"date": "2026-05-02", "service_name": "VMs", "pre_tax_cost": 80.0},
    ])
    resp = test_client.get("/api/billing/cost/daily?start_date=2026-05-01&end_date=2026-05-02", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["date"] == "2026-05-01"
    assert body[0]["total_cost"] == 120.0
    assert body[1]["date"] == "2026-05-02"
    assert body[1]["total_cost"] == 80.0


def test_cost_daily_filters_by_service_name(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_details"]._col.insert_many([
        {"date": "2026-05-01", "service_name": "VMs", "pre_tax_cost": 100.0},
        {"date": "2026-05-01", "service_name": "Storage", "pre_tax_cost": 20.0},
    ])
    resp = test_client.get("/api/billing/cost/daily?start_date=2026-05-01&end_date=2026-05-01&service_name=VMs", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["total_cost"] == 100.0


def test_cost_forecast_returns_forecast_rows(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_summary"]._col.insert_many([
        {"dimension": "Forecast", "period": "2026-06", "dimension_value": "VMs", "total_cost": 5000.0, "currency": "USD"},
        {"dimension": "ServiceName", "period": "2026-06", "dimension_value": "VMs", "total_cost": 4000.0, "currency": "USD"},
    ])
    resp = test_client.get("/api/billing/cost/forecast", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["dimension"] == "Forecast"


def test_list_alerts_returns_active_only(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_alerts"]._col.insert_many([
        {"alert_id": "a1", "status": "Active", "alert_name": "Budget Alert", "creation_time": "2026-06-01"},
        {"alert_id": "a2", "status": "Resolved", "alert_name": "Old Alert", "creation_time": "2026-05-01"},
    ])
    resp = test_client.get("/api/billing/alerts", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["alert_id"] == "a1"


def test_advisor_recommendations_with_category_filter(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_advisor_recommendations"]._col.insert_many([
        {"recommendation_id": "r1", "status": "Active", "category": "Cost", "impact": "High", "estimated_monthly_savings": 100.0},
        {"recommendation_id": "r2", "status": "Active", "category": "Security", "impact": "Low"},
        {"recommendation_id": "r3", "status": "Dismissed", "category": "Cost", "impact": "Medium"},
    ])
    resp = test_client.get("/api/billing/advisor/recommendations?category=Cost", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["recommendation_id"] == "r1"


def test_advisor_recommendations_with_impact_filter(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_advisor_recommendations"]._col.insert_many([
        {"recommendation_id": "r1", "status": "Active", "category": "Cost", "impact": "High", "estimated_monthly_savings": 100.0},
        {"recommendation_id": "r2", "status": "Active", "category": "Security", "impact": "Low"},
    ])
    resp = test_client.get("/api/billing/advisor/recommendations?impact=High", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["impact"] == "High"


def test_advisor_cost_savings_returns_only_cost_category(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_advisor_recommendations"]._col.insert_many([
        {"recommendation_id": "r1", "status": "Active", "category": "Cost", "impact": "High", "estimated_monthly_savings": 300.0},
        {"recommendation_id": "r2", "status": "Active", "category": "Security", "impact": "High"},
        {"recommendation_id": "r3", "status": "Dismissed", "category": "Cost", "impact": "Medium", "estimated_monthly_savings": 50.0},
    ])
    resp = test_client.get("/api/billing/advisor/cost-savings", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["category"] == "Cost"
    assert body[0]["estimated_monthly_savings"] == 300.0


def test_list_invoices_sorted_by_period_desc(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_invoices"]._col.insert_many([
        {"invoice_id": "inv-1", "billing_period_start": "2026-04-01", "billed_amount": 100.0},
        {"invoice_id": "inv-2", "billing_period_start": "2026-06-01", "billed_amount": 200.0},
        {"invoice_id": "inv-3", "billing_period_start": "2026-05-01", "billed_amount": 150.0},
    ])
    resp = test_client.get("/api/billing/invoices", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["invoice_id"] == "inv-2"
    assert body[1]["invoice_id"] == "inv-3"
    assert body[2]["invoice_id"] == "inv-1"


def test_get_invoice_returns_404_for_missing(test_client: TestClient):
    resp = test_client.get("/api/billing/invoices/NOPE", headers=AUTH)
    assert resp.status_code == 404


def test_get_invoice_returns_invoice_when_found(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_invoices"]._col.insert_one(
        {"invoice_id": "inv-1", "billing_period_start": "2026-05-01", "billed_amount": 100.0, "status": "Paid"}
    )
    resp = test_client.get("/api/billing/invoices/inv-1", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["invoice_id"] == "inv-1"
    assert body["status"] == "Paid"


def test_reservation_details_by_period(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_reservation_details"]._col.insert_many([
        {"reservation_id": "res-1", "billing_period": "2026-05", "sku_name": "D2s_v5"},
        {"reservation_id": "res-2", "billing_period": "2026-04", "sku_name": "F4s_v5"},
    ])
    resp = test_client.get("/api/billing/reservations/details?billing_period=2026-05", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["reservation_id"] == "res-1"


def test_reservation_recommendations_sorted_by_savings(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_reservation_recommendations"]._col.insert_many([
        {"sku_name": "D2s_v5", "net_savings": 1000.0, "term": "P1Y"},
        {"sku_name": "F4s_v5", "net_savings": 3000.0, "term": "P3Y"},
        {"sku_name": "E2s_v5", "net_savings": 500.0, "term": "P1Y"},
    ])
    resp = test_client.get("/api/billing/reservations/recommendations", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["net_savings"] == 3000.0
    assert body[1]["net_savings"] == 1000.0
    assert body[2]["net_savings"] == 500.0


def test_cost_summary_empty_returns_zero_total(test_client: TestClient):
    resp = test_client.get("/api/billing/cost/summary?period=2099-01", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0.0
    assert body["currency"] == "USD"
    assert body["items"] == []


def test_serialize_helper_with_objectid():
    from billing_routes import _serialize
    from bson import ObjectId
    doc = {"_id": ObjectId(), "name": "test"}
    result = _serialize(doc)
    assert "id" in result
    assert isinstance(result["id"], str)


def test_serialize_helper_with_empty():
    from billing_routes import _serialize
    assert _serialize(None) == {}
    assert _serialize({}) == {}


def test_current_period_format():
    from billing_routes import _current_period
    period = _current_period()
    assert len(period) == 7
    assert period[4] == "-"
