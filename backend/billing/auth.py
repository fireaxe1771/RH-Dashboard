import logging
from functools import lru_cache
from azure.identity import ClientSecretCredential
from config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_billing_credential() -> ClientSecretCredential:
    """Returns a cached ClientSecretCredential for billing API access.

    Uses lru_cache to ensure a single credential instance is shared across
    the application, allowing the azure-identity SDK to cache tokens internally.
    Raises BillingConfigError if required settings are missing.
    """
    from billing import BillingConfigError
    if not settings.AZURE_BILLING_CLIENT_ID or not settings.AZURE_BILLING_CLIENT_SECRET:
        raise BillingConfigError(
            "AZURE_BILLING_CLIENT_ID and AZURE_BILLING_CLIENT_SECRET must be set."
        )
    logger.info("Initializing Azure billing service principal credential...")
    return ClientSecretCredential(
        tenant_id=settings.AZURE_TENANT_ID,
        client_id=settings.AZURE_BILLING_CLIENT_ID,
        client_secret=settings.AZURE_BILLING_CLIENT_SECRET
    )


def get_billing_token() -> str:
    """Acquires a fresh bearer token for https://management.azure.com/.

    The credential handles token caching and automatic refresh internally.
    Returns the raw token string (without the 'Bearer ' prefix).
    """
    credential = get_billing_credential()
    token = credential.get_token("https://management.azure.com/.default")
    return token.token


def get_billing_auth_headers() -> dict[str, str]:
    """Returns a dict with Authorization and Content-Type headers for API calls."""
    return {
        "Authorization": f"Bearer {get_billing_token()}",
        "Content-Type": "application/json"
    }
