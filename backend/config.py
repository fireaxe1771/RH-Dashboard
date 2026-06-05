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
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # Metadata DB (MongoDB) configuration
    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "recoveryhub_dashboard")
    
    # Target SQL Database (Azure SQL) configuration
    AZURE_SQL_HOST: str = os.getenv("AZURE_SQL_HOST", "")
    AZURE_SQL_PORT: int = int(os.getenv("AZURE_SQL_PORT", "1433"))
    AZURE_SQL_DB: str = os.getenv("AZURE_SQL_DB", "")
    AZURE_SQL_USER: str = os.getenv("AZURE_SQL_USER", "")
    AZURE_SQL_PASSWORD: str = os.getenv("AZURE_SQL_PASSWORD", "")
    
    # Entra ID Authentication configuration
    AZURE_CLIENT_ID: str = os.getenv("AZURE_CLIENT_ID", "")
    AZURE_TENANT_ID: str = os.getenv("AZURE_TENANT_ID", "")

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
        if not self.AZURE_SQL_USER:
            missing.append("AZURE_SQL_USER")
        if not self.AZURE_SQL_PASSWORD:
            missing.append("AZURE_SQL_PASSWORD")
            
        # Check Authentication configuration
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

# Create and validate configurations globally
settings = Settings()

# In normal runtime (excluding automated testing where envs might be mocked), validate variables on import
if os.getenv("TESTING") != "true":
    settings.validate_settings()
