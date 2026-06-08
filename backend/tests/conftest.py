import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock

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


# ---------------------------------------------------------------------------
# Async wrappers around mongomock so that the application code (which uses Motor
# async semantics like ``await cursor.to_list()``, ``await collection.find_one()``)
# can work transparently with a synchronous in-memory mongomock backend.
# ---------------------------------------------------------------------------

class _AsyncCursor:
    """Wraps a mongomock Cursor to support ``await cursor.to_list(length=...)``."""

    def __init__(self, cursor):
        self._cursor = cursor

    async def to_list(self, length=None):
        return list(self._cursor)


class _AsyncCollection:
    """Wraps a mongomock Collection so that query/mutation methods are awaitable."""

    def __init__(self, collection):
        self._col = collection

    def find(self, *args, **kwargs):
        cursor = self._col.find(*args, **kwargs)
        return _AsyncSortableCursor(cursor)

    async def find_one(self, *args, **kwargs):
        return self._col.find_one(*args, **kwargs)

    async def insert_one(self, *args, **kwargs):
        return self._col.insert_one(*args, **kwargs)

    async def find_one_and_update(self, *args, **kwargs):
        from pymongo import ReturnDocument
        # mongomock uses return_document kwarg
        return self._col.find_one_and_update(*args, **kwargs)

    async def delete_one(self, *args, **kwargs):
        return self._col.delete_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return self._col.count_documents(*args, **kwargs)

    async def create_index(self, *args, **kwargs):
        return self._col.create_index(*args, **kwargs)


class _AsyncSortableCursor(_AsyncCursor):
    """Supports chaining ``.sort()`` before ``.to_list()``."""

    def __init__(self, cursor):
        super().__init__(cursor)

    def sort(self, *args, **kwargs):
        self._cursor = self._cursor.sort(*args, **kwargs)
        return self


class _AsyncDatabase:
    """Wraps a mongomock Database so that ``db["collection"]`` returns an _AsyncCollection."""

    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        return _AsyncCollection(self._db[name])

    def __getattr__(self, name):
        return _AsyncCollection(self._db[name])


@pytest.fixture
def mock_mongo_db():
    """Provides a mongomock client wrapped with async helpers to simulate Motor."""
    client = mongomock.MongoClient()
    db = client["test_db"]
    return _AsyncDatabase(db)


@pytest.fixture(autouse=True)
def mock_db_manager(monkeypatch, mock_mongo_db):
    """Intercepts db_manager references to route query operations to our mock mongo."""
    from database import db_manager

    # Override connect/disconnect
    db_manager.connect = MagicMock()
    db_manager.disconnect = MagicMock()
    db_manager.init_indexes = AsyncMock()
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
            "iss": "https://login.microsoftonline.com/mock_tenant/v2.0",
            "aud": "mock_client"
        }

    monkeypatch.setattr(TokenVerifier, "verify_token", mock_verify)


@pytest.fixture(autouse=True)
def reset_target_db_cache():
    """Ensures each test starts with a clean SQL column-resolution cache."""
    from target_db import target_db

    target_db._claims_date_column = None
    target_db._claims_column_map = None


@pytest.fixture
def test_client():
    """Provides a synchronous HTTP client for integration test endpoint hits."""
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app)
