"""Tests for billing.consumption — network mocked."""
import pytest

from billing import BillingAPIError, consumption
from tests.billing_http_mocks import FakeResponse, session_factory


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    monkeypatch.setattr(consumption, "get_billing_auth_headers", lambda: {"Authorization": "Bearer x"})


@pytest.mark.asyncio
async def test_get_reservation_details(monkeypatch):
    factory = session_factory(FakeResponse(200, {"value": [{"properties": {"reservationId": "r1"}}], "nextLink": None}))
    monkeypatch.setattr(consumption.aiohttp, "ClientSession", factory)
    rows = await consumption.get_reservation_details("/subscriptions/abc", "2026-05-01", "2026-05-31")
    assert rows[0]["properties"]["reservationId"] == "r1"


@pytest.mark.asyncio
async def test_get_reservation_recommendations(monkeypatch):
    factory = session_factory(FakeResponse(200, {"value": [{"properties": {"netSavings": 100}}], "nextLink": None}))
    monkeypatch.setattr(consumption.aiohttp, "ClientSession", factory)
    rows = await consumption.get_reservation_recommendations("/subscriptions/abc", "P1Y", "Last30Days")
    assert rows[0]["properties"]["netSavings"] == 100


@pytest.mark.asyncio
async def test_get_price_sheet_swallows_403(monkeypatch):
    factory = session_factory(FakeResponse(403, {"error": {"code": "NotSupported"}}))
    monkeypatch.setattr(consumption.aiohttp, "ClientSession", factory)
    rows = await consumption.get_price_sheet("/subscriptions/abc", "202605")
    assert rows == []
