"""Microsoft.Advisor REST API client — recommendations (especially Cost)."""
import asyncio
import logging

import aiohttp

from billing.auth import get_billing_auth_headers
from billing.cost_management import MANAGEMENT_BASE, _api_call_with_retry

logger = logging.getLogger(__name__)

ADVISOR_API_VERSION = "2025-01-01"
CATEGORIES = ["Cost", "Security", "Performance", "HighAvailability", "OperationalExcellence"]


async def get_recommendations(subscription_id: str, category: str) -> list[dict]:
    """GETs Advisor recommendations for a single category, following nextLink pagination."""
    url = (
        f"{MANAGEMENT_BASE}/subscriptions/{subscription_id}/providers/Microsoft.Advisor/"
        f"recommendations?api-version={ADVISOR_API_VERSION}&$filter=Category eq '{category}'"
    )
    headers = get_billing_auth_headers()
    results: list[dict] = []
    async with aiohttp.ClientSession() as session:
        next_url: str | None = url
        while next_url:
            payload = await _api_call_with_retry(session, "get", next_url, headers=headers)
            if not payload:
                break
            results.extend(payload.get("value", []))
            next_url = payload.get("nextLink")
    return results


async def get_all_recommendations(subscription_id: str) -> list[dict]:
    """Calls get_recommendations() for all 5 categories, pausing 1s between each."""
    combined: list[dict] = []
    for index, category in enumerate(CATEGORIES):
        if index > 0:
            await asyncio.sleep(1)  # Respect Advisor rate limits between categories
        combined.extend(await get_recommendations(subscription_id, category))
    return combined


def _extract_savings(extended_properties: dict) -> tuple[float | None, str | None]:
    """Parses savingsAmount/savingsCurrency from a recommendation's extendedProperties."""
    if not extended_properties:
        return None, None
    amount_raw = extended_properties.get("savingsAmount") or extended_properties.get("annualSavingsAmount")
    currency = extended_properties.get("savingsCurrency")
    if amount_raw is None:
        return None, currency
    try:
        return float(amount_raw), currency
    except (TypeError, ValueError):
        return None, currency
