"""Tests for billing.billing_accounts — network mocked."""
import pytest

from billing import billing_accounts
from tests.billing_http_mocks import FakeResponse, session_factory


@pytest.fixture(autouse=True)
def _mock_auth(monkeypatch):
    monkeypatch.setattr(billing_accounts, "get_billing_auth_headers", lambda: {"Authorization": "Bearer x"})


@pytest.mark.asyncio
async def test_get_billing_accounts(monkeypatch):
    factory = session_factory(FakeResponse(200, {"value": [{"name": "acct1"}]}))
    monkeypatch.setattr(billing_accounts.aiohttp, "ClientSession", factory)
    accounts = await billing_accounts.get_billing_accounts()
    assert accounts[0]["name"] == "acct1"


@pytest.mark.asyncio
async def test_get_invoices_mosp(monkeypatch):
    factory = session_factory(FakeResponse(200, {"value": [{"name": "INV-1"}]}))
    monkeypatch.setattr(billing_accounts.aiohttp, "ClientSession", factory)
    invoices = await billing_accounts.get_invoices("acct1", "MOSP")
    assert invoices[0]["name"] == "INV-1"
    # MOSP must hit the subscription-less invoices endpoint
    assert "Microsoft.Billing/invoices" in factory.session.calls[0][1]


@pytest.mark.asyncio
async def test_get_invoices_ea_uses_account_path(monkeypatch):
    factory = session_factory(FakeResponse(200, {"value": []}))
    monkeypatch.setattr(billing_accounts.aiohttp, "ClientSession", factory)
    await billing_accounts.get_invoices("acct1", "EA")
    assert "billingAccounts/acct1/invoices" in factory.session.calls[0][1]
