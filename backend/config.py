import os
import sys
from dotenv import load_dotenv

# Load local environment file if present (for local testing outside Docker or compose env setup)
load_dotenv()

class Settings:
    """Application configuration and validation manager.
    
    Fails loudly at startup if critical database or authentication parameters are omitted.
    """
    
    # Web server settings
    PORT: int = int(os.getenv("PORT", "8001"))
    
    # Metadata DB (MongoDB) configuration
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "recoveryhub_dashboard")
    
    # Target SQL Database (Azure SQL) configuration
    AZURE_SQL_HOST: str = os.getenv("AZURE_SQL_HOST", "")
    AZURE_SQL_PORT: int = int(os.getenv("AZURE_SQL_PORT", "1433"))
    AZURE_SQL_DB: str = os.getenv("AZURE_SQL_DB", "")
    AZURE_SQL_USER: str = os.getenv("AZURE_SQL_USER", "")
    AZURE_SQL_PASSWORD: str = os.getenv("AZURE_SQL_PASSWORD", "")
    AZURE_SQL_AUTHENTICATION: str = os.getenv("AZURE_SQL_AUTHENTICATION", "basic")  # 'basic' or 'azure-ad'
    AZURE_SQL_TENANT_ID: str = os.getenv("AZURE_SQL_TENANT_ID", "")
    
    # Entra ID Authentication configuration
    DEV_AUTH_BYPASS: bool = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
    AZURE_CLIENT_ID: str = os.getenv("AZURE_CLIENT_ID", "")
    AZURE_TENANT_ID: str = os.getenv("AZURE_TENANT_ID", "")

    # --- Azure Billing Integration ---
    AZURE_BILLING_CLIENT_ID: str = os.getenv("AZURE_BILLING_CLIENT_ID", "")
    AZURE_BILLING_CLIENT_SECRET: str = os.getenv("AZURE_BILLING_CLIENT_SECRET", "")
    AZURE_SUBSCRIPTION_ID: str = os.getenv("AZURE_SUBSCRIPTION_ID", "")
    AZURE_BILLING_ACCOUNT_ID: str = os.getenv("AZURE_BILLING_ACCOUNT_ID", "")
    AZURE_BILLING_ACCOUNT_TYPE: str = os.getenv("AZURE_BILLING_ACCOUNT_TYPE", "MOSP")
    AZURE_MANAGEMENT_GROUP_ID: str = os.getenv("AZURE_MANAGEMENT_GROUP_ID", "")

    # --- AI / Embeddings ---
    # When AZURE_OPENAI_ENDPOINT is set, the app uses Azure OpenAI (Foundry) and
    # OPENAI_CHAT_MODEL / OPENAI_EMBEDDING_MODEL are treated as Azure *deployment* names.
    # Otherwise it falls back to the OpenAI.com API using OPENAI_API_KEY.
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    OPENAI_CHAT_MODEL: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

    # --- Billing Sync Configuration ---
    BILLING_SYNC_ENABLED: bool = os.getenv("BILLING_SYNC_ENABLED", "true").lower() == "true"
    BILLING_DAILY_SYNC_HOUR: int = int(os.getenv("BILLING_DAILY_SYNC_HOUR", "2"))
    BILLING_HISTORY_MONTHS: int = int(os.getenv("BILLING_HISTORY_MONTHS", "12"))

    def validate_settings(self) -> None:
        """Validates configuration parameters, stopping startup if required variables are missing."""
        missing = []
        
        # Check MongoDB configuration
        if not self.MONGODB_URI:
            missing.append("MONGODB_URI")
            
        # Check SQL configuration
        if not self.AZURE_SQL_HOST:
            missing.append("AZURE_SQL_HOST")
        if not self.AZURE_SQL_DB:
            missing.append("AZURE_SQL_DB")
        
        # For basic auth, check username/password
        if self.AZURE_SQL_AUTHENTICATION == "basic":
            if not self.AZURE_SQL_USER:
                missing.append("AZURE_SQL_USER")
            if not self.AZURE_SQL_PASSWORD:
                missing.append("AZURE_SQL_PASSWORD")
        # For Azure AD auth, username/password are the Service Principal credentials
        elif self.AZURE_SQL_AUTHENTICATION == "azure-ad":
            if not self.AZURE_SQL_USER:
                missing.append("AZURE_SQL_USER")
            if not self.AZURE_SQL_PASSWORD:
                missing.append("AZURE_SQL_PASSWORD")
            if not self.AZURE_SQL_TENANT_ID:
                missing.append("AZURE_SQL_TENANT_ID")
            
        # Check Authentication configuration unless local development bypass is enabled
        if not self.DEV_AUTH_BYPASS:
            if not self.AZURE_CLIENT_ID:
                missing.append("AZURE_CLIENT_ID")
            if not self.AZURE_TENANT_ID:
                missing.append("AZURE_TENANT_ID")
            
        if missing:
            error_msg = (
                f"\nFATAL CONFIGURATION ERROR: The following required environment variables are missing:\n"
                f"{', '.join(missing)}\n"
                f"Please define them in your .env file or Azure Container App settings.\n"
                f"Halting server boot.\n"
            )
            # Print loudly to stdout/stderr and exit
            sys.stderr.write(error_msg)
            sys.stderr.flush()
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        # Billing variables are only required when the sync scheduler is enabled
        if self.BILLING_SYNC_ENABLED and os.getenv("TESTING") != "true":
            self.validate_billing_settings()

    def validate_billing_settings(self) -> None:
        """Validates billing service principal and AI credentials when sync is enabled."""
        missing = []
        if not self.AZURE_BILLING_CLIENT_ID:
            missing.append("AZURE_BILLING_CLIENT_ID")
        if not self.AZURE_BILLING_CLIENT_SECRET:
            missing.append("AZURE_BILLING_CLIENT_SECRET")
        if not self.AZURE_SUBSCRIPTION_ID:
            missing.append("AZURE_SUBSCRIPTION_ID")
        # AI credentials: Azure OpenAI (Foundry) takes precedence; otherwise OpenAI.com
        if self.AZURE_OPENAI_ENDPOINT:
            if not self.AZURE_OPENAI_API_KEY:
                missing.append("AZURE_OPENAI_API_KEY")
        elif not self.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY (or set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY)")
        if missing:
            raise ValueError(f"Missing required billing variables: {', '.join(missing)}")

# Create and validate configurations globally
settings = Settings()

# In normal runtime (excluding automated testing where envs might be mocked), validate variables on import
if os.getenv("TESTING") != "true":
    settings.validate_settings()
