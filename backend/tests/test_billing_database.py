import pytest

from database import DatabaseManager

BILLING_COLLECTIONS = [
    "azure_cost_details",
    "azure_cost_summary",
    "azure_invoices",
    "azure_budgets",
    "azure_advisor_recommendations",
    "azure_reservation_details",
    "azure_reservation_recommendations",
    "azure_resource_inventory",
    "azure_retail_prices",
    "azure_billing_sync_log",
    "azure_billing_vectors",
]


@pytest.mark.asyncio
async def test_init_indexes_creates_billing_collections(mock_mongo_db):
    manager = DatabaseManager()
    manager.db = mock_mongo_db

    # Should run without raising even though mongomock lacks list_search_indexes
    await manager.init_indexes()

    existing = mock_mongo_db._db.list_collection_names()
    for name in BILLING_COLLECTIONS:
        assert name in existing, f"Expected collection {name} to be created by index init"


@pytest.mark.asyncio
async def test_cost_summary_unique_index_present(mock_mongo_db):
    manager = DatabaseManager()
    manager.db = mock_mongo_db
    await manager.init_indexes()

    index_info = mock_mongo_db._db["azure_cost_summary"].index_information()
    unique_indexes = [name for name, spec in index_info.items() if spec.get("unique")]
    assert unique_indexes, "azure_cost_summary should have a unique index"


@pytest.mark.asyncio
async def test_check_vector_index_does_not_raise(mock_mongo_db):
    manager = DatabaseManager()
    manager.db = mock_mongo_db
    # mongomock collections have no list_search_indexes -> caught and warned, no raise
    await manager._check_vector_index()
