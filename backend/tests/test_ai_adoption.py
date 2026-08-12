import pytest
from unittest.mock import MagicMock, patch
import mongomock

from ai_adoption_service import (
    _classify_fees,
    get_ai_participation_map,
    get_department_draft_activity,
    get_ai_adoption_report,
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
    assert "TOP" not in query  # fetch ALL departments; slicing done in Python


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


def _mock_activity():
    """6 departments alternating AI / not-AI, sorted by submitted_drafts desc."""
    return [
        {"department_id": "1", "department_name": "FD 1", "state": "TX", "submitted_drafts": 100},
        {"department_id": "2", "department_name": "FD 2", "state": "CA", "submitted_drafts": 90},
        {"department_id": "3", "department_name": "FD 3", "state": "NY", "submitted_drafts": 80},
        {"department_id": "4", "department_name": "FD 4", "state": "FL", "submitted_drafts": 70},
        {"department_id": "5", "department_name": "FD 5", "state": "WA", "submitted_drafts": 60},
        {"department_id": "6", "department_name": "FD 6", "state": "OH", "submitted_drafts": 50},
    ]


def _mock_participation():
    """Odd-numbered departments use AI; even-numbered do not."""
    return {
        1: {"uses_ai": True, "ai_mode": "auto", "qualifying_fee_count": 1,
            "has_auto": True, "has_queued": False, "has_limited_auto": False,
            "department_name": "FD 1"},
        3: {"uses_ai": True, "ai_mode": "queued", "qualifying_fee_count": 1,
            "has_auto": False, "has_queued": True, "has_limited_auto": False,
            "department_name": "FD 3"},
        5: {"uses_ai": True, "ai_mode": "auto", "qualifying_fee_count": 1,
            "has_auto": True, "has_queued": False, "has_limited_auto": False,
            "department_name": "FD 5"},
    }


@pytest.mark.asyncio
async def test_all_tab_returns_top_n_regardless_of_ai_status():
    with patch("ai_adoption_service.get_department_draft_activity", return_value=_mock_activity()), \
         patch("ai_adoption_service.get_ai_participation_map", return_value=_mock_participation()):
        result = await get_ai_adoption_report(None, "2026-08-04", "2026-08-11", limit=2, ai_status="all")

    depts = result["departments"]
    assert len(depts) == 2
    assert depts[0]["department_id"] == "1"
    assert depts[0]["rank_overall"] == 1
    assert depts[1]["department_id"] == "2"
    assert depts[1]["rank_overall"] == 2


@pytest.mark.asyncio
async def test_using_ai_tab_is_independent_top_n():
    """Using AI tab must return the top N AI-using departments, not a filter
    of the all-departments top-N list (which would yield only 1 row here)."""
    with patch("ai_adoption_service.get_department_draft_activity", return_value=_mock_activity()), \
         patch("ai_adoption_service.get_ai_participation_map", return_value=_mock_participation()):
        result = await get_ai_adoption_report(None, "2026-08-04", "2026-08-11", limit=2, ai_status="using_ai")

    depts = result["departments"]
    assert len(depts) == 2
    # All returned departments must use AI
    assert all(d["ai_status"] == "using_ai" for d in depts)
    # Top 2 AI-using departments are #1 (100 drafts) and #3 (80 drafts)
    assert depts[0]["department_id"] == "1"
    assert depts[0]["rank_overall"] == 1
    assert depts[1]["department_id"] == "3"
    assert depts[1]["rank_overall"] == 2


@pytest.mark.asyncio
async def test_not_using_ai_tab_is_independent_top_n():
    """Not Using AI tab must return the top N non-AI departments, not a filter
    of the all-departments top-N list (which would yield only 1 row here)."""
    with patch("ai_adoption_service.get_department_draft_activity", return_value=_mock_activity()), \
         patch("ai_adoption_service.get_ai_participation_map", return_value=_mock_participation()):
        result = await get_ai_adoption_report(None, "2026-08-04", "2026-08-11", limit=2, ai_status="not_using_ai")

    depts = result["departments"]
    assert len(depts) == 2
    # All returned departments must NOT use AI
    assert all(d["ai_status"] == "not_using_ai" for d in depts)
    # Top 2 non-AI departments are #2 (90 drafts) and #4 (70 drafts)
    assert depts[0]["department_id"] == "2"
    assert depts[0]["rank_overall"] == 1
    assert depts[1]["department_id"] == "4"
    assert depts[1]["rank_overall"] == 2


@pytest.mark.asyncio
async def test_summary_reflects_all_departments_not_filtered_view():
    with patch("ai_adoption_service.get_department_draft_activity", return_value=_mock_activity()), \
         patch("ai_adoption_service.get_ai_participation_map", return_value=_mock_participation()):
        result = await get_ai_adoption_report(None, "2026-08-04", "2026-08-11", limit=2, ai_status="using_ai")

    summary = result["summary"]
    # All 6 departments are active, not just the 2 shown
    assert summary["active_departments"] == 6
    assert summary["departments_using_ai"] == 3
    assert summary["departments_not_using_ai"] == 3
