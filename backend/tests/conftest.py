import os
import sys
import pytest
from unittest.mock import MagicMock

# Force TESTING environment mode BEFORE importing app files to skip startup config checks
os.environ["TESTING"] = "true"
os.environ["MONGODB_URI"] = "mongodb://mock"
os.environ["AZURE_SQL_HOST"] = "mock_sql"
os.environ["AZURE_SQL_DB"] = "mock_db"
os.environ["AZURE_SQL_USER"] = "mock_user"
os.environ["AZURE_SQL_PASSWORD"] = "mock_pass"
os.environ["AZURE_CLIENT_ID"] = "mock_client"
os.environ["AZURE_TENANT_ID"] = "mock_tenant"

# Ensure backend directory is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock
from motor.motor_asyncio import AsyncIOMotorClient

@pytest.fixture
def mock_mongo_db():
    """Provides a synchronous mongomock client wrapped to simulate Motor responses."""
    client = mongomock.MongoClient()
    db = client["test_db"]
    return db

@pytest.fixture(autouse=True)
def mock_db_manager(monkeypatch, mock_mongo_db):
    """Intercepts db_manager references to route query operations to our mock mongo."""
    from database import db_manager
    
    # Override connect/disconnect
    db_manager.connect = MagicMock()
    db_manager.disconnect = MagicMock()
    db_manager.init_indexes = MagicMock()
    db_manager.db = mock_mongo_db
    
    # Force get_db dependency to yield the mock db
    monkeypatch.setattr("database.db_manager.db", mock_mongo_db)

@pytest.fixture(autouse=True)
def mock_entra_verification(monkeypatch):
    """Bypasses active Microsoft authentication redirects, returning standard test claims."""
    from auth import TokenVerifier
    
    # Mock JWKS fetches and decodes
    monkeypatch.setattr(TokenVerifier, "_fetch_jwks", lambda self: {"keys": [{"kid": "test-kid"}]})
    monkeypatch.setattr(TokenVerifier, "get_public_key", lambda self, kid: {"kid": kid})
    
    # Custom decode returns standard claims
    def mock_verify(self, token: str) -> dict:
        if token == "invalid-token":
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Token signature verification failed.")
        return {
            "preferred_username": "john.doe@streamlineas.com",
            "upn": "john.doe@streamlineas.com",
            "name": "John Doe",
            "iss": f"https://login.microsoftonline.com/mock_tenant/v2.0",
            "aud": "mock_client"
        }
        
    monkeypatch.setattr(TokenVerifier, "verify_token", mock_verify)

@pytest.fixture
def test_client():
    """Provides a synchronous HTTP client for integration test endpoint hits."""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)
