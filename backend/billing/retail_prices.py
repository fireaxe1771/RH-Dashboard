"""Azure Retail Prices API client — public pricing (no auth required)."""
import logging

import aiohttp

from billing.cost_management import _api_call_with_retry

logger = logging.getLogger(__name__)

RETAIL_PRICES_BASE = "https://prices.azure.com/api/retail/prices"
RETAIL_PRICES_API_VERSION = "2023-01-01-preview"

# Most commonly billed Azure services, fetched for a single region to keep volume sane.
COMMON_SERVICES = [
    "Virtual Machines",
    "Azure App Service",
    "Azure SQL Database",
    "Azure Cosmos DB",
    "Azure Kubernetes Service",
    "Azure Blob Storage",
    "Azure Container Apps",
    "Azure Monitor",
]
_DEFAULT_REGION = "eastus"


async def get_retail_prices(
    service_name: str | None = None,
    arm_region_name: str | None = None,
    currency_code: str = "USD",
) -> list[dict]:
    """GETs retail prices with optional filters, following NextPageLink pagination."""
    filters = []
    if service_name:
        filters.append(f"serviceName eq '{service_name}'")
    if arm_region_name:
        filters.append(f"armRegionName eq '{arm_region_name}'")
    filter_str = " and ".join(filters)

    url = f"{RETAIL_PRICES_BASE}?api-version={RETAIL_PRICES_API_VERSION}&currencyCode='{currency_code}'"
    if filter_str:
        url += f"&$filter={filter_str}"

    results: list[dict] = []
    async with aiohttp.ClientSession() as session:
        next_url: str | None = url
        while next_url:
            payload = await _api_call_with_retry(session, "get", next_url, headers={})
            if not payload:
                break
            results.extend(payload.get("Items", []))
            next_url = payload.get("NextPageLink")
    return results


async def sync_common_service_prices() -> list[dict]:
    """Fetches retail prices for the most commonly billed services in eastus."""
    combined: list[dict] = []
    for service in COMMON_SERVICES:
        combined.extend(await get_retail_prices(service_name=service, arm_region_name=_DEFAULT_REGION))
    return combined
