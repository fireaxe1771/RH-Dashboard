"""Tests for config.Settings validation logic."""
import os
import pytest
from unittest.mock import patch


@pytest.fixture
def fresh_settings():
    """Creates a Settings instance with test env vars for each test."""
    with patch.dict(os.environ, {
        "TESTING": "true",
        "MONGODB_URI": "mongodb://mock",
        "AZURE_SQL_HOST": "mock_host",
        "AZURE_SQL_DB": "mock_db",
        "AZURE_SQL_USER": "mock_user",
        "AZURE_SQL_PASSWORD": "mock_pass",
        "AZURE_CLIENT_ID": "mock_client",
        "AZURE_TENANT_ID": "mock_tenant",
        "DEV_AUTH_BYPASS": "false",
        "BILLING_SYNC_ENABLED": "false",
    }):
        # Re-import to get fresh settings
        import importlib
        import config
        importlib.reload(config)
        yield config.Settings()


class TestSettingsValidation:
    def test_validate_settings_passes_with_all_required(self, fresh_settings):
        """Should not raise when all required settings are present."""
        fresh_settings.validate_settings()

    def test_validate_settings_raises_on_missing_mongodb(self):
        s = SettingsStub(MONGODB_URI="")
        with pytest.raises(ValueError, match="MONGODB_URI"):
            s.validate_settings()

    def test_validate_settings_raises_on_missing_sql_host(self):
        s = SettingsStub(AZURE_SQL_HOST="")
        with pytest.raises(ValueError, match="AZURE_SQL_HOST"):
            s.validate_settings()

    def test_validate_settings_raises_on_missing_sql_db(self):
        s = SettingsStub(AZURE_SQL_DB="")
        with pytest.raises(ValueError, match="AZURE_SQL_DB"):
            s.validate_settings()

    def test_validate_settings_basic_auth_requires_user_password(self):
        s = SettingsStub(
            AZURE_SQL_AUTHENTICATION="basic",
            AZURE_SQL_USER="",
            AZURE_SQL_PASSWORD="",
        )
        with pytest.raises(ValueError, match="AZURE_SQL_USER"):
            s.validate_settings()

    def test_validate_settings_azure_ad_auth_requires_tenant(self):
        s = SettingsStub(
            AZURE_SQL_AUTHENTICATION="azure-ad",
            AZURE_SQL_USER="sp-id",
            AZURE_SQL_PASSWORD="secret",
            AZURE_SQL_TENANT_ID="",
        )
        with pytest.raises(ValueError, match="AZURE_SQL_TENANT_ID"):
            s.validate_settings()

    def test_validate_settings_skips_auth_when_bypass_enabled(self):
        s = SettingsStub(
            DEV_AUTH_BYPASS=True,
            AZURE_CLIENT_ID="",
            AZURE_TENANT_ID="",
        )
        s.validate_settings()

    def test_validate_settings_raises_on_missing_client_id(self):
        s = SettingsStub(
            DEV_AUTH_BYPASS=False,
            AZURE_CLIENT_ID="",
        )
        with pytest.raises(ValueError, match="AZURE_CLIENT_ID"):
            s.validate_settings()

    def test_validate_settings_raises_on_missing_tenant_id(self):
        s = SettingsStub(
            DEV_AUTH_BYPASS=False,
            AZURE_CLIENT_ID="client",
            AZURE_TENANT_ID="",
        )
        with pytest.raises(ValueError, match="AZURE_TENANT_ID"):
            s.validate_settings()


class TestBillingSettingsValidation:
    def test_billing_validation_raises_on_missing_billing_client_id(self):
        s = SettingsStub(
            BILLING_SYNC_ENABLED=True,
            AZURE_BILLING_CLIENT_ID="",
            AZURE_BILLING_CLIENT_SECRET="secret",
            AZURE_SUBSCRIPTION_ID="sub",
            OPENAI_API_KEY="sk-test",
        )
        with pytest.raises(ValueError, match="AZURE_BILLING_CLIENT_ID"):
            s.validate_billing_settings()

    def test_billing_validation_raises_on_missing_billing_secret(self):
        s = SettingsStub(
            AZURE_BILLING_CLIENT_ID="client",
            AZURE_BILLING_CLIENT_SECRET="",
            AZURE_SUBSCRIPTION_ID="sub",
            OPENAI_API_KEY="sk-test",
        )
        with pytest.raises(ValueError, match="AZURE_BILLING_CLIENT_SECRET"):
            s.validate_billing_settings()

    def test_billing_validation_raises_on_missing_subscription(self):
        s = SettingsStub(
            AZURE_BILLING_CLIENT_ID="client",
            AZURE_BILLING_CLIENT_SECRET="secret",
            AZURE_SUBSCRIPTION_ID="",
            OPENAI_API_KEY="sk-test",
        )
        with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
            s.validate_billing_settings()

    def test_billing_validation_passes_with_openai_api_key(self):
        s = SettingsStub(
            AZURE_BILLING_CLIENT_ID="client",
            AZURE_BILLING_CLIENT_SECRET="secret",
            AZURE_SUBSCRIPTION_ID="sub",
            OPENAI_API_KEY="sk-test",
            AZURE_OPENAI_ENDPOINT="",
        )
        s.validate_billing_settings()

    def test_billing_validation_passes_with_azure_openai(self):
        s = SettingsStub(
            AZURE_BILLING_CLIENT_ID="client",
            AZURE_BILLING_CLIENT_SECRET="secret",
            AZURE_SUBSCRIPTION_ID="sub",
            OPENAI_API_KEY="",
            AZURE_OPENAI_ENDPOINT="https://my.openai.azure.com/",
            AZURE_OPENAI_API_KEY="azure-key",
        )
        s.validate_billing_settings()

    def test_billing_validation_raises_on_missing_azure_openai_key(self):
        s = SettingsStub(
            AZURE_BILLING_CLIENT_ID="client",
            AZURE_BILLING_CLIENT_SECRET="secret",
            AZURE_SUBSCRIPTION_ID="sub",
            OPENAI_API_KEY="",
            AZURE_OPENAI_ENDPOINT="https://my.openai.azure.com/",
            AZURE_OPENAI_API_KEY="",
        )
        with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
            s.validate_billing_settings()

    def test_billing_validation_raises_on_missing_all_ai_keys(self):
        s = SettingsStub(
            AZURE_BILLING_CLIENT_ID="client",
            AZURE_BILLING_CLIENT_SECRET="secret",
            AZURE_SUBSCRIPTION_ID="sub",
            OPENAI_API_KEY="",
            AZURE_OPENAI_ENDPOINT="",
        )
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            s.validate_billing_settings()


class SettingsStub:
    """Minimal stub that mimics config.Settings for validation method testing."""
    PORT = 8001
    MONGODB_URI = "mongodb://mock"
    MONGODB_DB_NAME = "test"
    AZURE_SQL_HOST = "host"
    AZURE_SQL_PORT = 1433
    AZURE_SQL_DB = "db"
    AZURE_SQL_USER = "user"
    AZURE_SQL_PASSWORD = "pass"
    AZURE_SQL_AUTHENTICATION = "basic"
    AZURE_SQL_TENANT_ID = "tenant"
    DEV_AUTH_BYPASS = True
    AZURE_CLIENT_ID = "client"
    AZURE_TENANT_ID = "tenant"
    AZURE_BILLING_CLIENT_ID = "billing-client"
    AZURE_BILLING_CLIENT_SECRET = "secret"
    AZURE_SUBSCRIPTION_ID = "sub"
    AZURE_BILLING_ACCOUNT_ID = "acct"
    AZURE_BILLING_ACCOUNT_TYPE = "MOSP"
    AZURE_MANAGEMENT_GROUP_ID = ""
    OPENAI_API_KEY = ""
    OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
    OPENAI_CHAT_MODEL = "gpt-4o-mini"
    AZURE_OPENAI_ENDPOINT = ""
    AZURE_OPENAI_API_KEY = ""
    AZURE_OPENAI_API_VERSION = "2024-10-21"
    BILLING_SYNC_ENABLED = False
    BILLING_DAILY_SYNC_HOUR = 2
    BILLING_HISTORY_MONTHS = 12

    def __init__(self, **overrides):
        for key, value in overrides.items():
            setattr(self, key, value)

    def validate_settings(self) -> None:
        from config import Settings
        # Copy validation logic by delegating to a real Settings instance
        # with our attribute values patched in
        import os
        old_testing = os.environ.get("TESTING")
        os.environ["TESTING"] = "true"
        # Build a real Settings and override its attributes
        real = Settings()
        for attr in dir(self):
            if not attr.startswith("_") and not callable(getattr(self, attr)):
                setattr(real, attr, getattr(self, attr))
        try:
            real.validate_settings()
        finally:
            if old_testing is not None:
                os.environ["TESTING"] = old_testing
            else:
                os.environ.pop("TESTING", None)

    def validate_billing_settings(self) -> None:
        from config import Settings
        import os
        old_testing = os.environ.get("TESTING")
        os.environ["TESTING"] = "true"
        real = Settings()
        for attr in dir(self):
            if not attr.startswith("_") and not callable(getattr(self, attr)):
                setattr(real, attr, getattr(self, attr))
        try:
            real.validate_billing_settings()
        finally:
            if old_testing is not None:
                os.environ["TESTING"] = old_testing
            else:
                os.environ.pop("TESTING", None)
