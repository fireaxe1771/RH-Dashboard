"""Tests for billing.retail_prices — network mocked (public API, no auth)."""
import pytest

from billing import retail_prices
from tests.billing_http_mocks import FakeResponse, session_factory


@pytest.mark.asyncio
async def test_get_retail_prices_paginates(monkeypatch):
    page1 = {"Items": [{"meterId": "m1"}], "NextPageLink": "https://next"}
    page2 = {"Items": [{"meterId": "m2"}], "NextPageLink": None}
    factory = session_factory(FakeResponse(200, page1), FakeResponse(200, page2))
    monkeypatch.setattr(retail_prices.aiohttp, "ClientSession", factory)
    rows = await retail_prices.get_retail_prices(service_name="Virtual Machines", arm_region_name="eastus")
    assert [r["meterId"] for r in rows] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_sync_common_service_prices(monkeypatch):
    responses = [FakeResponse(200, {"Items": [{"meterId": f"m{i}"}], "NextPageLink": None}) for i in range(len(retail_prices.COMMON_SERVICES))]
    factory = session_factory(*responses)
    monkeypatch.setattr(retail_prices.aiohttp, "ClientSession", factory)
    rows = await retail_prices.sync_common_service_prices()
    assert len(rows) == len(retail_prices.COMMON_SERVICES)
