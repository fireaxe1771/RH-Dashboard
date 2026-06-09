"""Microsoft.ResourceGraph REST API client — KQL resource inventory queries."""
import logging

import aiohttp

from billing.auth import get_billing_auth_headers
from billing.cost_management import MANAGEMENT_BASE, _api_call_with_retry

logger = logging.getLogger(__name__)

RESOURCE_GRAPH_API_VERSION = "2021-03-01"

KQL_ALL_RESOURCES = """
Resources
| project id, name, type, location, resourceGroup, subscriptionId, sku, tags, kind, properties
| where type !startswith 'microsoft.resources/deployments'
| order by type asc
"""

KQL_DEALLOCATED_VMS = """
Resources
| where type == 'microsoft.compute/virtualmachines'
| extend powerState = tostring(properties.extended.instanceView.powerState.code)
| where powerState =~ 'PowerState/deallocated' or powerState =~ 'PowerState/stopped'
| project id, name, resourceGroup, location, skuName = sku.name, powerState, tags
"""

KQL_UNTAGGED_RESOURCES = """
Resources
| where isnull(tags) or array_length(bag_keys(tags)) == 0
| where type !startswith 'microsoft.resources'
| project id, name, type, resourceGroup, location
"""


async def query_resources(
    subscription_ids: list[str], kql_query: str, page_size: int = 1000
) -> list[dict]:
    """POSTs a KQL query to Resource Graph, handling $skipToken pagination."""
    url = f"{MANAGEMENT_BASE}/providers/Microsoft.ResourceGraph/resources?api-version={RESOURCE_GRAPH_API_VERSION}"
    headers = get_billing_auth_headers()
    results: list[dict] = []
    skip_token: str | None = None
    async with aiohttp.ClientSession() as session:
        while True:
            options: dict = {"$top": page_size}
            if skip_token:
                options["$skipToken"] = skip_token
            body = {
                "subscriptions": subscription_ids,
                "query": kql_query,
                "options": options,
            }
            payload = await _api_call_with_retry(session, "post", url, json=body, headers=headers)
            if not payload:
                break
            results.extend(payload.get("data", []))
            skip_token = payload.get("$skipToken") or payload.get("skipToken")
            if not skip_token:
                break
    return results
