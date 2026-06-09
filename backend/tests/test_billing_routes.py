"""Integration tests for /api/billing/* endpoints via TestClient."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer valid-mock-token"}


def test_endpoints_require_auth(test_client: TestClient):
    for path in ["/api/billing/sync/status", "/api/billing/cost/summary", "/api/billing/budgets"]:
        assert test_client.get(path).status_code == 401


def test_cost_summary_aggregates(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_summary"]._col.insert_many([
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": "VMs", "total_cost": 100.0, "currency": "USD"},
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": "Storage", "total_cost": 25.0, "currency": "USD"},
    ])
    resp = test_client.get("/api/billing/cost/summary?period=2026-05", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 125.0
    assert body["currency"] == "USD"
    assert len(body["items"]) == 2


def test_top_spenders_limit(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_cost_summary"]._col.insert_many([
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": f"S{i}", "total_cost": float(i), "currency": "USD"}
        for i in range(5)
    ])
    resp = test_client.get("/api/billing/cost/top-spenders?period=2026-05&limit=2", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_budgets_listing(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_budgets"]._col.insert_one(
        {"budget_id": "b1", "budget_name": "Prod", "amount": 1000.0, "utilization_pct": 80.0}
    )
    resp = test_client.get("/api/billing/budgets", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()[0]["budget_name"] == "Prod"


def test_advisor_summary_shape(test_client: TestClient):
    from database import db_manager
    db_manager.db["azure_advisor_recommendations"]._col.insert_many([
        {"recommendation_id": "r1", "status": "Active", "category": "Cost", "impact": "High", "estimated_monthly_savings": 100.0, "savings_currency": "USD"},
        {"recommendation_id": "r2", "status": "Active", "category": "Security", "impact": "Low"},
    ])
    resp = test_client.get("/api/billing/advisor/summary", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_recommendations"] == 2
    assert body["cost_recommendations"] == 1
    assert body["total_monthly_savings"] == 100.0
    assert body["by_impact"]["High"] == 1


def test_sync_status_dedupes_by_type(test_client: TestClient):
    from database import db_manager
    from datetime import datetime, timezone
    db_manager.db["azure_billing_sync_log"]._col.insert_many([
        {"sync_type": "cost_details_daily", "status": "completed", "started_at": datetime(2026, 5, 1, tzinfo=timezone.utc), "records_synced": 10},
        {"sync_type": "cost_details_daily", "status": "failed", "started_at": datetime(2026, 5, 2, tzinfo=timezone.utc), "records_synced": 0},
    ])
    resp = test_client.get("/api/billing/sync/status", headers=AUTH)
    assert resp.status_code == 200
    syncs = resp.json()["syncs"]
    entry = next(s for s in syncs if s["sync_type"] == "cost_details_daily")
    # Most recent (2026-05-02, failed) should win
    assert entry["status"] == "failed"


def test_trigger_sync_rejects_invalid_type(test_client: TestClient):
    resp = test_client.post("/api/billing/sync/trigger", json={"sync_type": "bogus"}, headers=AUTH)
    assert resp.status_code == 400


def test_trigger_sync_queues_valid_type(test_client: TestClient, monkeypatch):
    import billing_routes
    monkeypatch.setitem(billing_routes._SYNC_DISPATCH, "daily", lambda db: AsyncMock()())
    resp = test_client.post("/api/billing/sync/trigger", json={"sync_type": "daily"}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "sync_type": "daily"}


def test_invoice_404(test_client: TestClient):
    resp = test_client.get("/api/billing/invoices/NOPE", headers=AUTH)
    assert resp.status_code == 404


def test_ai_query_no_data(test_client: TestClient, monkeypatch):
    import billing_routes
    monkeypatch.setattr(billing_routes.vectorizer, "semantic_search", AsyncMock(return_value=[]))
    resp = test_client.post("/api/billing/ai/query", json={"question": "why are costs high?"}, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] == []
    assert "trigger a billing sync" in body["answer"]


def test_ai_query_with_sources(test_client: TestClient, monkeypatch):
    import billing_routes
    docs = [{
        "document_type": "top_spenders",
        "text": "VMs cost $100",
        "metadata": {"period": "2026-05", "dimension_value": "all_services", "total_cost": 100.0},
        "score": 0.9,
    }]
    monkeypatch.setattr(billing_routes.vectorizer, "semantic_search", AsyncMock(return_value=docs))

    chat_client = MagicMock()
    msg = MagicMock()
    msg.content = "Your VMs are the top cost driver at $100."
    choice = MagicMock(message=msg)
    chat_client.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[choice]))
    monkeypatch.setattr(billing_routes.vectorizer, "_get_openai_client", lambda: chat_client)

    resp = test_client.post("/api/billing/ai/query", json={"question": "what drives cost?"}, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert "top cost driver" in body["answer"]
    assert len(body["sources"]) == 1
    assert body["sources"][0]["document_type"] == "top_spenders"
