import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def billing_auth(monkeypatch):
    """Imports billing.auth with valid billing settings and a cleared credential cache."""
    from config import settings
    monkeypatch.setattr(settings, "AZURE_BILLING_CLIENT_ID", "billing-client-id")
    monkeypatch.setattr(settings, "AZURE_BILLING_CLIENT_SECRET", "billing-secret")
    monkeypatch.setattr(settings, "AZURE_TENANT_ID", "tenant-id")

    import billing.auth as auth_module
    importlib.reload(auth_module)
    auth_module.get_billing_credential.cache_clear()
    yield auth_module
    auth_module.get_billing_credential.cache_clear()


def test_get_billing_credential_returns_client_secret_credential(billing_auth):
    with patch("billing.auth.ClientSecretCredential") as mock_cred_cls:
        instance = MagicMock()
        mock_cred_cls.return_value = instance

        credential = billing_auth.get_billing_credential()

        assert credential is instance
        mock_cred_cls.assert_called_once_with(
            tenant_id="tenant-id",
            client_id="billing-client-id",
            client_secret="billing-secret",
        )


def test_get_billing_token_uses_management_scope(billing_auth):
    token_obj = MagicMock()
    token_obj.token = "fake-bearer-token"
    credential = MagicMock()
    credential.get_token.return_value = token_obj

    with patch("billing.auth.ClientSecretCredential", return_value=credential):
        token = billing_auth.get_billing_token()

    assert token == "fake-bearer-token"
    credential.get_token.assert_called_once_with("https://management.azure.com/.default")


def test_get_billing_auth_headers(billing_auth):
    token_obj = MagicMock()
    token_obj.token = "header-token"
    credential = MagicMock()
    credential.get_token.return_value = token_obj

    with patch("billing.auth.ClientSecretCredential", return_value=credential):
        headers = billing_auth.get_billing_auth_headers()

    assert headers["Authorization"] == "Bearer header-token"
    assert headers["Content-Type"] == "application/json"


def test_missing_client_id_raises_config_error(billing_auth, monkeypatch):
    from config import settings
    from billing import BillingConfigError
    monkeypatch.setattr(settings, "AZURE_BILLING_CLIENT_ID", "")
    billing_auth.get_billing_credential.cache_clear()

    with pytest.raises(BillingConfigError):
        billing_auth.get_billing_credential()
