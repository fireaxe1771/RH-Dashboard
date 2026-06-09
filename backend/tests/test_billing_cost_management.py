"""Tests for billing.cost_management — all network calls mocked."""
import pytest

from billing import BillingAPIError, cost_management
from tests.billing_http_mocks import FakeResponse, session_factory


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    monkeypatch.setattr(cost_management, "get_billing_auth_headers", lambda: {"Authorization": "Bearer x"})


@pytest.mark.asyncio
async def test_query_costs_parses_rows(monkeypatch):
    payload = {
        "properties": {
            "columns": [
                {"name": "PreTaxCost", "type": "Number"},
                {"name": "ServiceName", "type": "String"},
                {"name": "Currency", "type": "String"},
            ],
            "rows": [[4821.44, "Virtual Machines", "USD"], [1203.12, "App Service", "USD"]],
            "nextLink": None,
        }
    }
    factory = session_factory(FakeResponse(200, payload))
    monkeypatch.setattr(cost_management.aiohttp, "ClientSession", factory)

    rows = await cost_management.query_costs("/subscriptions/abc", "2026-05-01", "2026-05-31", group_by_dimensions=["ServiceName"])

    assert len(rows) == 2
    assert rows[0]["ServiceName"] == "Virtual Machines"
    assert rows[0]["PreTaxCost"] == 4821.44


@pytest.mark.asyncio
async def test_query_costs_follows_next_link(monkeypatch):
    page1 = {"properties": {"columns": [{"name": "PreTaxCost"}], "rows": [[1.0]], "nextLink": "https://next"}}
    page2 = {"properties": {"columns": [{"name": "PreTaxCost"}], "rows": [[2.0]], "nextLink": None}}
    factory = session_factory(FakeResponse(200, page1), FakeResponse(200, page2))
    monkeypatch.setattr(cost_management.aiohttp, "ClientSession", factory)

    rows = await cost_management.query_costs("/subscriptions/abc", "2026-05-01", "2026-05-31")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_generate_cost_details_report_polls_then_downloads(monkeypatch):
    submit = FakeResponse(202, headers={"Location": "https://op", "Retry-After": "30"})
    running = FakeResponse(202, headers={"Location": "https://op2"})
    csv_text = "SubscriptionId,Date,ResourceId,MeterId,ChargeType,Cost\nsub1,2026-05-01,/r/vm1,m1,Usage,12.50\n"
    complete = FakeResponse(200, {"properties": {"manifest": {"blobs": [{"blobLink": "https://blob/report.csv"}]}}})
    blob = FakeResponse(200, text_data=csv_text)

    factory = session_factory(submit, running, complete, blob)
    monkeypatch.setattr(cost_management.aiohttp, "ClientSession", factory)
    # Skip the 30s polling sleeps
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(cost_management.asyncio, "sleep", _no_sleep)

    rows = await cost_management.generate_cost_details_report("/subscriptions/abc", "2026-05-01", "2026-05-31")
    assert len(rows) == 1
    assert rows[0]["Cost"] == "12.50"
    assert rows[0]["MeterId"] == "m1"


@pytest.mark.asyncio
async def test_api_call_retries_on_429(monkeypatch):
    factory = session_factory(
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(200, {"properties": {"columns": [], "rows": []}}),
    )
    monkeypatch.setattr(cost_management.aiohttp, "ClientSession", factory)

    async def _no_sleep(_):
        return None
    monkeypatch.setattr(cost_management.asyncio, "sleep", _no_sleep)

    rows = await cost_management.query_costs("/subscriptions/abc", "2026-05-01", "2026-05-31")
    assert rows == []


@pytest.mark.asyncio
async def test_api_call_raises_on_403(monkeypatch):
    factory = session_factory(FakeResponse(403, {"error": {"code": "AuthorizationFailed"}}))
    monkeypatch.setattr(cost_management.aiohttp, "ClientSession", factory)

    with pytest.raises(BillingAPIError) as exc:
        await cost_management.get_budgets("/subscriptions/abc")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_budgets_returns_value_list(monkeypatch):
    factory = session_factory(FakeResponse(200, {"value": [{"name": "Prod-Monthly"}]}))
    monkeypatch.setattr(cost_management.aiohttp, "ClientSession", factory)
    budgets = await cost_management.get_budgets("/subscriptions/abc")
    assert budgets[0]["name"] == "Prod-Monthly"
