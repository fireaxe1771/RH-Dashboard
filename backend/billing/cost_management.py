"""Microsoft.CostManagement REST API client.

All calls to the ``Microsoft.CostManagement`` namespace live here. Functions
return structured Python dicts/lists — they perform no MongoDB writes.
"""
import asyncio
import csv
import io
import logging

import aiohttp

from billing.auth import get_billing_auth_headers

logger = logging.getLogger(__name__)

COST_MANAGEMENT_API_VERSION = "2025-03-01"
MANAGEMENT_BASE = "https://management.azure.com"
MAX_RETRIES = 5

# Cost details report polling
_POLL_INTERVAL_SECONDS = 30
_POLL_TIMEOUT_SECONDS = 15 * 60


async def _api_call_with_retry(session: aiohttp.ClientSession, method: str, url: str, **kwargs) -> dict:
    """Executes an aiohttp request with exponential backoff on 429/503.

    Raises BillingAPIError after MAX_RETRIES attempts or on unrecoverable errors.
    """
    from billing import BillingAPIError
    for attempt in range(MAX_RETRIES):
        async with getattr(session, method)(url, **kwargs) as response:
            if response.status == 200:
                return await response.json()
            if response.status == 202:
                return {
                    "status": 202,
                    "location": response.headers.get("Location"),
                    "retry_after": response.headers.get("Retry-After", "30"),
                }
            if response.status in (429, 503):
                retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                wait = min(retry_after, 60)
                logger.warning(
                    f"Rate limited (HTTP {response.status}). Waiting {wait}s before "
                    f"retry {attempt + 1}/{MAX_RETRIES}."
                )
                await asyncio.sleep(wait)
                continue
            if response.status == 403:
                body = await response.json()
                error_code = body.get("error", {}).get("code", "Unknown")
                raise BillingAPIError(
                    f"Authorization denied (HTTP 403): {error_code}. Verify RBAC roles are assigned.",
                    status_code=403,
                    error_code=error_code,
                )
            if response.status == 404:
                logger.warning(f"Resource not found (HTTP 404) at {url}. Returning empty result.")
                return {}
            body = await response.json()
            error_msg = body.get("error", {}).get("message", "Unknown error")
            raise BillingAPIError(f"API error (HTTP {response.status}): {error_msg}", status_code=response.status)
    raise BillingAPIError(f"Max retries ({MAX_RETRIES}) exceeded for {url}")


def _rows_to_dicts(properties: dict) -> list[dict]:
    """Converts a CostManagement query/forecast ``columns``+``rows`` payload to dicts."""
    columns = [c.get("name") for c in properties.get("columns", [])]
    rows = properties.get("rows", [])
    return [dict(zip(columns, row)) for row in rows]


async def query_costs(
    scope: str,
    start_date: str,
    end_date: str,
    granularity: str = "Monthly",
    group_by_dimensions: list[str] | None = None,
) -> list[dict]:
    """POSTs an aggregated cost query and returns one dict per result row."""
    url = f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.CostManagement/query?api-version={COST_MANAGEMENT_API_VERSION}"
    grouping = [{"type": "Dimension", "name": dim} for dim in (group_by_dimensions or [])]
    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start_date}T00:00:00+00:00",
            "to": f"{end_date}T00:00:00+00:00",
        },
        "dataset": {
            "granularity": granularity,
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
        },
    }
    if grouping:
        body["dataset"]["grouping"] = grouping

    headers = get_billing_auth_headers()
    results: list[dict] = []
    async with aiohttp.ClientSession() as session:
        next_url: str | None = url
        next_body: dict | None = body
        while next_url:
            payload = await _api_call_with_retry(session, "post", next_url, json=next_body, headers=headers)
            properties = payload.get("properties", {})
            results.extend(_rows_to_dicts(properties))
            next_url = properties.get("nextLink")
            next_body = {} if next_url else None
    return results


async def generate_cost_details_report(
    scope: str,
    start_date: str,
    end_date: str,
    metric: str = "ActualCost",
) -> list[dict]:
    """Submits an async cost details report, polls to completion, and parses the CSV.

    Returns one dict per CSV row keyed by the original CSV column names.
    """
    from billing import BillingAPIError

    submit_url = (
        f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.CostManagement/"
        f"generateCostDetailsReport?api-version={COST_MANAGEMENT_API_VERSION}"
    )
    body = {"metric": metric, "timePeriod": {"start": start_date, "end": end_date}}
    headers = get_billing_auth_headers()

    async with aiohttp.ClientSession() as session:
        submit = await _api_call_with_retry(session, "post", submit_url, json=body, headers=headers)
        location = submit.get("location")
        if not location:
            # Some responses return the manifest synchronously (small reports)
            manifest = submit.get("properties", {}).get("manifest")
            if manifest:
                return await _download_and_parse(session, manifest)
            logger.warning("Cost details report submission returned no operation Location header.")
            return []

        elapsed = 0
        while elapsed < _POLL_TIMEOUT_SECONDS:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS
            poll = await _api_call_with_retry(session, "get", location, headers=headers)
            if poll.get("status") == 202:
                location = poll.get("location") or location
                continue
            manifest = poll.get("properties", {}).get("manifest")
            if manifest:
                return await _download_and_parse(session, manifest)
        raise BillingAPIError(
            f"Cost details report timed out after {_POLL_TIMEOUT_SECONDS}s for scope {scope}."
        )


async def _download_and_parse(session: aiohttp.ClientSession, manifest: dict) -> list[dict]:
    """Downloads each blob in the manifest (SAS URLs, no auth) and parses CSV rows."""
    rows: list[dict] = []
    for blob in manifest.get("blobs", []):
        blob_link = blob.get("blobLink")
        if not blob_link:
            continue
        async with session.get(blob_link) as response:
            if response.status != 200:
                logger.warning(f"Failed to download cost details blob (HTTP {response.status}).")
                continue
            text = await response.text()
        reader = csv.DictReader(io.StringIO(text))
        rows.extend(dict(row) for row in reader)
    return rows


async def get_forecast(scope: str, start_date: str, end_date: str) -> list[dict]:
    """POSTs a cost forecast query and returns predicted cost rows."""
    url = f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.CostManagement/forecast?api-version={COST_MANAGEMENT_API_VERSION}"
    body = {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {
            "from": f"{start_date}T00:00:00+00:00",
            "to": f"{end_date}T00:00:00+00:00",
        },
        "dataset": {
            "granularity": "Monthly",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
        },
        "includeActualCost": False,
        "includeFreshPartialCost": False,
    }
    headers = get_billing_auth_headers()
    async with aiohttp.ClientSession() as session:
        payload = await _api_call_with_retry(session, "post", url, json=body, headers=headers)
    return _rows_to_dicts(payload.get("properties", {}))


async def get_budgets(scope: str) -> list[dict]:
    """GETs all budgets for the scope. Returns [] if none (404)."""
    url = f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.CostManagement/budgets?api-version={COST_MANAGEMENT_API_VERSION}"
    headers = get_billing_auth_headers()
    async with aiohttp.ClientSession() as session:
        payload = await _api_call_with_retry(session, "get", url, headers=headers)
    return payload.get("value", []) if payload else []


async def get_alerts(scope: str) -> list[dict]:
    """GETs all cost alerts for the scope. Returns [] if none."""
    url = f"{MANAGEMENT_BASE}/{scope.strip('/')}/providers/Microsoft.CostManagement/alerts?api-version={COST_MANAGEMENT_API_VERSION}"
    headers = get_billing_auth_headers()
    async with aiohttp.ClientSession() as session:
        payload = await _api_call_with_retry(session, "get", url, headers=headers)
    return payload.get("value", []) if payload else []
