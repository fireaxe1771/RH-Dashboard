"""Tests for billing.resource_graph — network mocked."""
import pytest

from billing import resource_graph
from tests.billing_http_mocks import FakeResponse, session_factory


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    monkeypatch.setattr(resource_graph, "get_billing_auth_headers", lambda: {"Authorization": "Bearer x"})


@pytest.mark.asyncio
async def test_query_resources_single_page(monkeypatch):
    factory = session_factory(FakeResponse(200, {"data": [{"id": "/r/1"}, {"id": "/r/2"}]}))
    monkeypatch.setattr(resource_graph.aiohttp, "ClientSession", factory)
    rows = await resource_graph.query_resources(["sub1"], resource_graph.KQL_ALL_RESOURCES)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_query_resources_paginates_skiptoken(monkeypatch):
    page1 = {"data": [{"id": "/r/1"}], "$skipToken": "tok"}
    page2 = {"data": [{"id": "/r/2"}]}
    factory = session_factory(FakeResponse(200, page1), FakeResponse(200, page2))
    monkeypatch.setattr(resource_graph.aiohttp, "ClientSession", factory)
    rows = await resource_graph.query_resources(["sub1"], resource_graph.KQL_ALL_RESOURCES)
    assert len(rows) == 2
    # Second request body should include the skip token
    second_call_kwargs = factory.session.calls[1][2]
    assert second_call_kwargs["json"]["options"]["$skipToken"] == "tok"
