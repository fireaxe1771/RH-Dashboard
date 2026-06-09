"""Microsoft.Consumption REST API client — reservations, marketplace, price sheet."""
import logging

import aiohttp

from billing.auth import get_billing_auth_headers
from billing.cost_management import MANAGEMENT_BASE, _api_call_with_retry

logger = logging.getLogger(__name__)

CONSUMPTION_API_VERSION = "2023-05-01"


async def _get_paginated(url: str) -> list[dict]:
    """Shared GET + nextLink pagination helper for Consumption endpoints."""
    headers = get_billing_auth_headers()
    results: list[dict] = []
    async with aiohttp.ClientSession() as session:
        next_url: str | None = url
        while next_url:
            payload = await _api_call_with_retry(session, "get", next_url, headers=headers)
            if not payload:
                break
            results.extend(payload.get("value", []))
            next_url = payload.get("nextLink") or payload.get("properties", {}).get("nextLink")
    return results


async def get_reservation_details(scope: str, start_date: str, end_date: str) -> list[dict]:
    """GETs daily reservation utilization details for the scope and date range."""
    url = (
        f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.Consumption/"
        f"reservationDetails?api-version={CONSUMPTION_API_VERSION}"
        f"&$filter=properties/usageDate ge {start_date} AND properties/usageDate le {end_date}"
    )
    return await _get_paginated(url)


async def get_reservation_summaries(scope: str, grain: str) -> list[dict]:
    """GETs reservation summaries at the given grain ('Daily' or 'Monthly')."""
    url = (
        f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.Consumption/"
        f"reservationSummaries?api-version={CONSUMPTION_API_VERSION}&grain={grain}"
    )
    return await _get_paginated(url)


async def get_reservation_recommendations(
    scope: str, term: str, look_back_period: str
) -> list[dict]:
    """GETs reservation purchase recommendations for a term and look-back window."""
    url = (
        f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.Consumption/"
        f"reservationRecommendations?api-version={CONSUMPTION_API_VERSION}"
        f"&$filter=properties/term eq '{term}' AND properties/lookBackPeriod eq '{look_back_period}'"
    )
    return await _get_paginated(url)


async def get_marketplace_charges(scope: str, start_date: str, end_date: str) -> list[dict]:
    """GETs marketplace charges for the scope and date range."""
    url = (
        f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.Consumption/"
        f"marketplaces?api-version={CONSUMPTION_API_VERSION}"
        f"&$filter=properties/usageStart ge '{start_date}' AND properties/usageEnd le '{end_date}'"
    )
    return await _get_paginated(url)


async def get_price_sheet(scope: str, billing_period: str) -> list[dict]:
    """EA/MCA only. Handles 403 gracefully (returns [] with a warning). Not for MOSP."""
    from billing import BillingAPIError

    url = (
        f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.Billing/"
        f"billingPeriods/{billing_period}/providers/Microsoft.Consumption/"
        f"pricesheets/default?api-version={CONSUMPTION_API_VERSION}"
    )
    try:
        return await _get_paginated(url)
    except BillingAPIError as exc:
        if exc.status_code == 403:
            logger.warning("Price sheet not available for this account type (HTTP 403). Returning [].")
            return []
        raise
