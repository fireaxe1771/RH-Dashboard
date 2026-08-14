"""Application configuration loaded from environment variables.

Defines the ``Settings`` class that centralises all runtime configuration
(database connections, Entra ID auth, Azure billing credentials, AI provider
settings, vectorizer limits) with validation at startup. Environment
variables are loaded from ``.env`` for local development.
"""
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
    # AI fee/resource configuration is maintained in the production AI database,
    # separate from the dashboard metadata database.
    RECOVERYHUB_AI_MONGODB_DB_NAME: str = os.getenv(
        "RECOVERYHUB_AI_MONGODB_DB_NAME",
        "AI_FEE_CALC_MULTI_AGENT_PROD",
    )
    
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

    # Frontend origin used to restrict CORS in production. Defaults to the
    # local dev origin; must be set to the deployed frontend FQDN in prod.
    # Must be a valid origin (scheme + host, no path, no trailing slash).
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # --- JWKS fetch resilience ---
    # Configurable so deployments in high-latency regions can tune without
    # code changes. Defaults are conservative (2 attempts, 15s timeout, 1s
    # backoff) and suit most Azure Container Apps environments.
    JWKS_FETCH_MAX_ATTEMPTS: int = int(os.getenv("JWKS_FETCH_MAX_ATTEMPTS", "2"))
    JWKS_FETCH_TIMEOUT: int = int(os.getenv("JWKS_FETCH_TIMEOUT", "15"))
    JWKS_FETCH_BACKOFF: int = int(os.getenv("JWKS_FETCH_BACKOFF", "1"))

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

    # --- Vectorizer rate limiting and cost controls ---
    # Max documents to embed in a single run_vectorization call. Prevents
    # runaway costs on large billing periods. 0 = no limit.
    VECTORIZER_MAX_DOCUMENTS: int = int(os.getenv("VECTORIZER_MAX_DOCUMENTS", "5000"))
    # Batch size for embedding API calls. OpenAI supports up to 2048 inputs
    # per request, but 100 is conservative for token-limit safety.
    VECTORIZER_BATCH_SIZE: int = int(os.getenv("VECTORIZER_BATCH_SIZE", "100"))
    # Seconds to sleep between batches (rate limiting). Set to 0 to disable.
    VECTORIZER_BATCH_DELAY: float = float(os.getenv("VECTORIZER_BATCH_DELAY", "0.5"))
    # Max retries on transient embedding API errors (429, 500, 503, timeout).
    VECTORIZER_MAX_RETRIES: int = int(os.getenv("VECTORIZER_MAX_RETRIES", "3"))
    # Minimum text length (chars) to warrant embedding. Shorter texts produce
    # low-quality vectors and waste API quota.
    VECTORIZER_MIN_TEXT_LENGTH: int = int(os.getenv("VECTORIZER_MIN_TEXT_LENGTH", "10"))

    # --- Billing Sync Configuration ---
    BILLING_SYNC_ENABLED: bool = os.getenv("BILLING_SYNC_ENABLED", "false").lower() == "true"
    BILLING_DAILY_SYNC_HOUR: int = int(os.getenv("BILLING_DAILY_SYNC_HOUR", "2"))
    BILLING_HISTORY_MONTHS: int = int(os.getenv("BILLING_HISTORY_MONTHS", "12"))

    # --- AI Analytics Worker ---
    # The AI Analytics Worker is an event-driven projection service that reads
    # RecoveryHub_AI MongoDB and writes analytics projections into the
    # dashboard-owned MongoDB. See docs/ai-analytics/PHASE_0_IMPLEMENTATION_PLAN.md.
    # When disabled, the worker task is not started in the FastAPI lifespan.
    AI_ANALYTICS_WORKER_ENABLED: bool = os.getenv("AI_ANALYTICS_WORKER_ENABLED", "false").lower() == "true"
    # Worker code version — stamped on every projection and worker-state record
    # so projections can be associated with the code that produced them.
    AI_ANALYTICS_WORKER_VERSION: str = os.getenv("AI_ANALYTICS_WORKER_VERSION", "0.1.0")
    # Projection schema version — bumped when the Section 9 projection schema
    # changes (see Section 9.12 Schema Evolution Policy). Old projections keep
    # their old version and are upgraded lazily.
    AI_ANALYTICS_WORKER_PROJECTION_SCHEMA_VERSION: int = int(
        os.getenv("AI_ANALYTICS_WORKER_PROJECTION_SCHEMA_VERSION", "1")
    )
    # Coalescing debounce window (seconds). Multiple change events for the same
    # claim within this window collapse into a single refresh (Phase 6/8).
    WORKER_DEBOUNCE_SECONDS: float = float(os.getenv("WORKER_DEBOUNCE_SECONDS", "2.0"))
    # Max claims processed per worker cycle. Prevents the worker from
    # monopolizing the FastAPI event loop during backfill bursts (Section 1.1.5).
    WORKER_MAX_CLAIMS_PER_CYCLE: int = int(os.getenv("WORKER_MAX_CLAIMS_PER_CYCLE", "100"))
    # Per-source-query timeout in milliseconds, enforced by the source
    # repository wrapper (Phase 2). Prevents a stuck Mongo query from starving
    # the event loop.
    WORKER_SOURCE_QUERY_TIMEOUT_MS: int = int(os.getenv("WORKER_SOURCE_QUERY_TIMEOUT_MS", "5000"))
    # Reconciliation safety-net cadence in minutes (Section 8.6). Every interval,
    # the worker queries ai_line_items for records changed since the last
    # checkpoint and requeues them for refresh.
    WORKER_RECONCILIATION_INTERVAL_MINUTES: int = int(os.getenv("WORKER_RECONCILIATION_INTERVAL_MINUTES", "30"))
    # Backfill batch size for historical population (Phase 4/6) and for
    # stale-checkpoint date-range fallback (Phase 9).
    WORKER_BACKFILL_BATCH_SIZE: int = int(os.getenv("WORKER_BACKFILL_BATCH_SIZE", "500"))
    # Max retries for a single claim refresh before escalating to the
    # dead-letter collection (Phase 5). Exponential backoff between attempts.
    WORKER_MAX_RETRIES: int = int(os.getenv("WORKER_MAX_RETRIES", "3"))
    # Attempt count after which a failing claim is moved to the dead-letter
    # collection. Defaults to matching WORKER_MAX_RETRIES.
    WORKER_DEAD_LETTER_THRESHOLD: int = int(os.getenv("WORKER_DEAD_LETTER_THRESHOLD", "3"))

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

        # Validate FRONTEND_URL format: must be a valid origin (scheme + host,
        # no path, no trailing slash). A malformed value would silently break
        # CORS at runtime, which is hard to debug.
        if self.FRONTEND_URL:
            if not (self.FRONTEND_URL.startswith("http://") or self.FRONTEND_URL.startswith("https://")):
                missing.append("FRONTEND_URL (must start with http:// or https://)")
            else:
                # Strip the scheme and check that no path or trailing slash
                # remains — a bare origin is "host" only, not "host/" or "host/path".
                stripped = self.FRONTEND_URL.split("://", 1)[1]
                if not stripped:
                    missing.append("FRONTEND_URL (must include a host after the scheme)")
                elif "/" in stripped:
                    missing.append("FRONTEND_URL (must be origin only: scheme + host, no path or trailing slash)")

        # Validate JWKS fetch resilience parameters: must be positive integers.
        if self.JWKS_FETCH_MAX_ATTEMPTS < 1:
            missing.append("JWKS_FETCH_MAX_ATTEMPTS (must be >= 1)")
        if self.JWKS_FETCH_TIMEOUT < 1:
            missing.append("JWKS_FETCH_TIMEOUT (must be >= 1 second)")
        if self.JWKS_FETCH_BACKOFF < 0:
            missing.append("JWKS_FETCH_BACKOFF (must be >= 0 seconds)")

        # Validate vectorizer parameters
        if self.VECTORIZER_MAX_DOCUMENTS < 0:
            missing.append("VECTORIZER_MAX_DOCUMENTS (must be >= 0)")
        if self.VECTORIZER_BATCH_SIZE < 1:
            missing.append("VECTORIZER_BATCH_SIZE (must be >= 1)")
        if self.VECTORIZER_BATCH_DELAY < 0:
            missing.append("VECTORIZER_BATCH_DELAY (must be >= 0)")
        if self.VECTORIZER_MAX_RETRIES < 0:
            missing.append("VECTORIZER_MAX_RETRIES (must be >= 0)")
        if self.VECTORIZER_MIN_TEXT_LENGTH < 0:
            missing.append("VECTORIZER_MIN_TEXT_LENGTH (must be >= 0)")

        # Validate AI Analytics Worker parameters
        if self.AI_ANALYTICS_WORKER_PROJECTION_SCHEMA_VERSION < 1:
            missing.append("AI_ANALYTICS_WORKER_PROJECTION_SCHEMA_VERSION (must be >= 1)")
        if self.WORKER_DEBOUNCE_SECONDS < 0:
            missing.append("WORKER_DEBOUNCE_SECONDS (must be >= 0)")
        if self.WORKER_MAX_CLAIMS_PER_CYCLE < 1:
            missing.append("WORKER_MAX_CLAIMS_PER_CYCLE (must be >= 1)")
        if self.WORKER_SOURCE_QUERY_TIMEOUT_MS < 1:
            missing.append("WORKER_SOURCE_QUERY_TIMEOUT_MS (must be >= 1)")
        if self.WORKER_RECONCILIATION_INTERVAL_MINUTES < 1:
            missing.append("WORKER_RECONCILIATION_INTERVAL_MINUTES (must be >= 1)")
        if self.WORKER_BACKFILL_BATCH_SIZE < 1:
            missing.append("WORKER_BACKFILL_BATCH_SIZE (must be >= 1)")
        if self.WORKER_MAX_RETRIES < 0:
            missing.append("WORKER_MAX_RETRIES (must be >= 0)")
        if self.WORKER_DEAD_LETTER_THRESHOLD < 1:
            missing.append("WORKER_DEAD_LETTER_THRESHOLD (must be >= 1)")
        if not self.AI_ANALYTICS_WORKER_VERSION:
            missing.append("AI_ANALYTICS_WORKER_VERSION (must not be empty)")

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
