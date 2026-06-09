"""Tests for billing.advisor — network mocked."""
import pytest

from billing import advisor
from tests.billing_http_mocks import FakeResponse, session_factory


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    monkeypatch.setattr(advisor, "get_billing_auth_headers", lambda: {"Authorization": "Bearer x"})


@pytest.mark.asyncio
async def test_get_recommendations_paginates(monkeypatch):
    page1 = {"value": [{"name": "rec1"}], "nextLink": "https://next"}
    page2 = {"value": [{"name": "rec2"}], "nextLink": None}
    factory = session_factory(FakeResponse(200, page1), FakeResponse(200, page2))
    monkeypatch.setattr(advisor.aiohttp, "ClientSession", factory)

    recs = await advisor.get_recommendations("sub1", "Cost")
    assert [r["name"] for r in recs] == ["rec1", "rec2"]


@pytest.mark.asyncio
async def test_get_all_recommendations_iterates_categories(monkeypatch):
    # 5 categories -> 5 single-page responses
    responses = [FakeResponse(200, {"value": [{"name": f"rec{i}"}], "nextLink": None}) for i in range(5)]
    factory = session_factory(*responses)
    monkeypatch.setattr(advisor.aiohttp, "ClientSession", factory)

    async def _no_sleep(_):
        return None
    monkeypatch.setattr(advisor.asyncio, "sleep", _no_sleep)

    recs = await advisor.get_all_recommendations("sub1")
    assert len(recs) == 5


def test_extract_savings_parses_amount():
    amount, currency = advisor._extract_savings({"savingsAmount": "158.40", "savingsCurrency": "USD"})
    assert amount == 158.40
    assert currency == "USD"


def test_extract_savings_handles_missing():
    amount, currency = advisor._extract_savings({})
    assert amount is None
    assert currency is None
