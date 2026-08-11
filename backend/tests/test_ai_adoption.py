import pytest
from unittest.mock import MagicMock, patch
import mongomock

from ai_adoption_service import (
    _classify_fees,
    get_ai_participation_map,
    get_department_draft_activity,
    AI_SEND_OPTIONS,
)


def test_classify_empty_fees_not_using_ai():
    assert _classify_fees([]) == {
        "uses_ai": False,
        "ai_mode": "not_using_ai",
        "qualifying_fee_count": 0,
        "has_auto": False,
        "has_queued": False,
        "has_limited_auto": False,
    }


def test_classify_disabled_send_option_not_using_ai():
    fees = [{"use_in_ai_process": True, "fee_send_option": "disabled"}]
    assert _classify_fees(fees)["uses_ai"] is False
    assert _classify_fees(fees)["ai_mode"] == "not_using_ai"


def test_classify_auto_only():
    fees = [{"use_in_ai_process": True, "fee_send_option": "auto"}]
    result = _classify_fees(fees)
    assert result["uses_ai"] is True
    assert result["ai_mode"] == "auto"
    assert result["has_auto"] is True
    assert result["has_queued"] is False


def test_classify_queued_only():
    fees = [{"use_in_ai_process": True, "fee_send_option": "queued"}]
    result = _classify_fees(fees)
    assert result["ai_mode"] == "queued"
    assert result["has_queued"] is True


def test_classify_mixed_mode():
    fees = [
        {"use_in_ai_process": True, "fee_send_option": "auto"},
        {"use_in_ai_process": True, "fee_send_option": "queued"},
    ]
    result = _classify_fees(fees)
    assert result["ai_mode"] == "mixed"
    assert result["qualifying_fee_count"] == 2


def test_department_activity_matches_claims_submitted_tile_query():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.description = None
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("ai_adoption_service.target_db._get_connection", return_value=connection):
        get_department_draft_activity("2026-08-04", "2026-08-11")

    query = cursor.execute.call_args.args[0]
    assert "c.submitted = 1" in query
    assert "c.original_run_id IS NULL" in query
    assert "c.date_of_submitted BETWEEN %(start_date)s AND %(end_date)s" in query
    assert "created BETWEEN" not in query
    assert "ORDER BY submitted_drafts DESC" in query


@pytest.mark.asyncio
async def test_get_ai_participation_map_returns_qualifying_departments_only():
    client = mongomock.MongoClient()
    db = client["test_ai"]
    db["department_fees_resources"].insert_many([
        {
            "department_id": 123,
            "department_name": "Test FD",
            "fees_resources_final": [
                {"use_in_ai_process": True, "fee_send_option": "auto"},
                {"use_in_ai_process": False, "fee_send_option": "disabled"},
            ],
        },
        {
            "department_id": 456,
            "department_name": "No AI FD",
            "fees_resources_final": [
                {"use_in_ai_process": True, "fee_send_option": "disabled"},
            ],
        },
    ])

    class _AsyncCursor:
        def __init__(self, items):
            self._items = items

        async def to_list(self, length=None):
            return self._items

    class _AsyncCol:
        def __init__(self, col):
            self._col = col

        def find(self, *args, **kwargs):
            return _AsyncCursor(list(self._col.find(*args, **kwargs)))

    class _AsyncDb:
        def __init__(self, db):
            self._db = db

        def __getitem__(self, name):
            return _AsyncCol(self._db[name])

        async def list_collection_names(self):
            return self._db.list_collection_names()

    ai_db = _AsyncDb(db)
    result = await get_ai_participation_map(ai_db)

    assert 123 in result
    assert 456 not in result
    assert result[123]["uses_ai"] is True
    assert result[123]["ai_mode"] == "auto"
