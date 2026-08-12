"""Unit tests for ai_analytics.sql_repository and mongo_repository.

Uses mocks for database connections — no real SQL/Mongo connections needed.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from ai_analytics.sql_repository import (
    get_ai_invoice_cohort,
    get_ai_invoice_record_for_claim,
    get_cancellation_details_for_claims,
    get_cancellation_details_for_claim,
    get_process_logs_for_claims,
    get_process_logs_for_claim,
    get_final_line_items,
    get_final_resource_line_items,
    get_department_lookup,
    get_cancellation_reason_inventory,
)
from ai_analytics.mongo_repository import (
    get_ai_line_items_for_claim_ids,
    get_ai_line_items_for_claim,
    get_agent_conversations_for_claim,
    get_conversation_by_id,
    check_ai_db_available,
    AI_LINE_ITEMS_COLLECTION,
    AGENT_CONVERSATIONS_COLLECTION,
)


# ---------------------------------------------------------------------------
# SQL repository tests
# ---------------------------------------------------------------------------

class TestSqlRepositoryMocked:
    """Tests that mock the target_db connection to verify SQL query logic."""

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_ai_invoice_cohort_returns_rows(self, mock_target_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_target_db._get_connection.return_value = mock_conn

        # _execute_parameterized calls target_db._execute_query(cursor, query, params)
        # which in turn calls cursor.execute. Mock _execute_query to call cursor.execute.
        def mock_execute_query(cursor, query, params):
            cursor.execute(query, params)
        mock_target_db._execute_query = mock_execute_query

        mock_cursor.description = [
            ("claim_id",), ("AI_inv_process_status",), ("dept_id",),
        ]
        mock_cursor.fetchall.return_value = [
            (12345, 4, 100),
            (67890, 2, 200),
        ]

        result = get_ai_invoice_cohort(
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        assert len(result) == 2
        assert result[0]["claim_id"] == 12345
        assert result[0]["AI_inv_process_status"] == 4
        assert mock_cursor.execute.called

        get_ai_invoice_cohort(date_basis="claim_created_date")
        query = mock_cursor.execute.call_args[0][0]
        assert "c.created >= %(start_date)s" in query

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_ai_invoice_record_for_claim_found(self, mock_target_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_target_db._get_connection.return_value = mock_conn

        def mock_execute_query(cursor, query, params):
            cursor.execute(query, params)
        mock_target_db._execute_query = mock_execute_query

        mock_cursor.description = [
            ("claim_id",), ("AI_inv_process_status",), ("dept_id",),
            ("department_name",), ("department_state",),
        ]
        mock_cursor.fetchall.return_value = [
            (12345, 4, 100, "FD1", "TX"),
        ]

        result = get_ai_invoice_record_for_claim(12345)
        assert result is not None
        assert result["claim_id"] == 12345
        assert result["AI_inv_process_status"] == 4
        assert result["department_name"] == "FD1"

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_ai_invoice_record_for_claim_not_found(self, mock_target_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_target_db._get_connection.return_value = mock_conn

        def mock_execute_query(cursor, query, params):
            cursor.execute(query, params)
        mock_target_db._execute_query = mock_execute_query

        mock_cursor.description = [
            ("claim_id",), ("AI_inv_process_status",),
        ]
        mock_cursor.fetchall.return_value = []

        result = get_ai_invoice_record_for_claim(999999)
        assert result is None

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_cancellation_details_for_claims_empty(self, mock_target_db):
        result = get_cancellation_details_for_claims([])
        assert result == {}
        assert not mock_target_db._get_connection.called

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_process_logs_for_claims_empty(self, mock_target_db):
        result = get_process_logs_for_claims([])
        assert result == {}
        assert not mock_target_db._get_connection.called

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_final_line_items(self, mock_target_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_target_db._get_connection.return_value = mock_conn

        mock_cursor.description = [
            ("claim_service_id",), ("claim_id",), ("item",), ("rate",), ("quantity",),
        ]
        mock_cursor.fetchall.return_value = [
            (1, 12345, "Level 1", 500.0, 1.0),
        ]

        result = get_final_line_items(12345)
        assert len(result) == 1
        assert result[0]["item"] == "Level 1"

    @patch("ai_analytics.sql_repository.target_db")
    def test_get_department_lookup_all(self, mock_target_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_target_db._get_connection.return_value = mock_conn

        mock_cursor.description = [
            ("department_id",), ("department_name",), ("state",),
        ]
        mock_cursor.fetchall.return_value = [
            (100, "Test FD", "TX"),
            (200, "Other FD", "CA"),
        ]

        result = get_department_lookup()
        assert len(result) == 2
        assert 100 in result
        assert result[100]["department_name"] == "Test FD"


# ---------------------------------------------------------------------------
# Mongo repository tests
# ---------------------------------------------------------------------------

class TestMongoRepositoryMocked:
    """Tests that mock the Motor MongoDB client."""

    @pytest.mark.asyncio
    async def test_get_ai_line_items_for_claim_ids_empty(self):
        ai_db = AsyncMock()
        result = await get_ai_line_items_for_claim_ids(ai_db, [])
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_ai_line_items_for_claim_ids(self):
        ai_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.to_list.return_value = [
            {"_id": "abc", "claim_id": 12345, "claim_processing_status": "COMPLETED"},
            {"_id": "def", "claim_id": 67890, "claim_processing_status": "INITIATED"},
        ]
        ai_db[AI_LINE_ITEMS_COLLECTION].find.return_value = mock_cursor

        result = await get_ai_line_items_for_claim_ids(ai_db, [12345, 67890])
        assert len(result) == 2
        assert 12345 in result
        assert result[12345]["claim_processing_status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_get_ai_line_items_for_claim_ids_string_claim_id(self):
        """Verify string claim_ids are normalized to int in the result."""
        ai_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.to_list.return_value = [
            {"_id": "abc", "claim_id": "12345", "claim_processing_status": "COMPLETED"},
        ]
        ai_db[AI_LINE_ITEMS_COLLECTION].find.return_value = mock_cursor

        result = await get_ai_line_items_for_claim_ids(ai_db, [12345])
        assert 12345 in result  # int key, not string

    @pytest.mark.asyncio
    async def test_get_ai_line_items_for_claim(self):
        ai_db = AsyncMock()
        # find_one is async, so set its return_value as a coroutine result
        ai_db[AI_LINE_ITEMS_COLLECTION].find_one = AsyncMock(return_value={
            "_id": "abc",
            "claim_id": 12345,
            "line_items": [{"item": "Level 1"}],
        })

        result = await get_ai_line_items_for_claim(ai_db, 12345)
        assert result is not None
        assert result["claim_id"] == 12345

    @pytest.mark.asyncio
    async def test_get_agent_conversations_for_claim(self):
        ai_db = AsyncMock()
        mock_cursor = MagicMock()
        mock_sort_cursor = AsyncMock()
        mock_sort_cursor.to_list.return_value = [
            {"_id": "conv1", "agent": "multi_agent_workflow", "status": "completed"},
        ]
        mock_cursor.sort.return_value = mock_sort_cursor
        ai_db[AGENT_CONVERSATIONS_COLLECTION].find.return_value = mock_cursor

        result = await get_agent_conversations_for_claim(ai_db, 12345)
        assert len(result) == 1
        assert result[0]["agent"] == "multi_agent_workflow"

    @pytest.mark.asyncio
    async def test_check_ai_db_available_true(self):
        ai_db = AsyncMock()
        ai_db.list_collection_names.return_value = [
            "ai_line_items", "ai_agent_conversations"
        ]
        result = await check_ai_db_available(ai_db)
        assert result is True

    @pytest.mark.asyncio
    async def test_check_ai_db_available_false(self):
        ai_db = AsyncMock()
        ai_db.list_collection_names.return_value = ["other_collection"]
        result = await check_ai_db_available(ai_db)
        assert result is False

    @pytest.mark.asyncio
    async def test_check_ai_db_available_error(self):
        ai_db = AsyncMock()
        ai_db.list_collection_names.side_effect = Exception("connection failed")
        result = await check_ai_db_available(ai_db)
        assert result is False
