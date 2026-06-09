"""Microsoft.Billing REST API client — accounts, periods, invoices."""
import logging

import aiohttp

from billing.auth import get_billing_auth_headers
from billing.cost_management import MANAGEMENT_BASE, _api_call_with_retry

logger = logging.getLogger(__name__)

BILLING_API_VERSION = "2020-05-01"
INVOICE_API_VERSION = "2020-05-01"


async def get_billing_accounts() -> list[dict]:
    """GETs all billing accounts visible to the service principal."""
    url = f"{MANAGEMENT_BASE}/providers/Microsoft.Billing/billingAccounts?api-version={BILLING_API_VERSION}"
    headers = get_billing_auth_headers()
    async with aiohttp.ClientSession() as session:
        payload = await _api_call_with_retry(session, "get", url, headers=headers)
    return payload.get("value", []) if payload else []


async def get_billing_periods(subscription_id: str) -> list[dict]:
    """GETs billing periods for a subscription, sorted by date descending."""
    url = (
        f"{MANAGEMENT_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Billing/"
        f"billingPeriods?api-version=2018-03-01-preview"
    )
    headers = get_billing_auth_headers()
    async with aiohttp.ClientSession() as session:
        payload = await _api_call_with_retry(session, "get", url, headers=headers)
    periods = payload.get("value", []) if payload else []
    return sorted(
        periods,
        key=lambda p: p.get("properties", {}).get("billingPeriodEndDate", ""),
        reverse=True,
    )


async def get_invoices(billing_account_id: str, billing_account_type: str) -> list[dict]:
    """Routes to the correct invoices endpoint based on billing account type."""
    headers = get_billing_auth_headers()
    account_type = (billing_account_type or "MOSP").upper()
    if account_type == "EA":
        url = (
            f"{MANAGEMENT_BASE}/providers/Microsoft.Billing/billingAccounts/"
            f"{billing_account_id}/invoices?api-version={INVOICE_API_VERSION}"
        )
    elif account_type == "MCA":
        # MCA requires a billing profile; callers should pass the composite id.
        url = (
            f"{MANAGEMENT_BASE}/providers/Microsoft.Billing/billingAccounts/"
            f"{billing_account_id}/invoices?api-version={INVOICE_API_VERSION}"
        )
    else:  # MOSP
        url = (
            f"{MANAGEMENT_BASE}/providers/Microsoft.Billing/invoices?"
            f"api-version={INVOICE_API_VERSION}"
        )
    async with aiohttp.ClientSession() as session:
        payload = await _api_call_with_retry(session, "get", url, headers=headers)
    return payload.get("value", []) if payload else []


async def get_invoice_transactions(billing_account_id: str, invoice_id: str) -> list[dict]:
    """MCA/MPA only. Returns [] for EA/MOSP with a warning log."""
    logger.warning(
        "get_invoice_transactions is only supported for MCA/MPA accounts; returning empty list."
    )
    return []
