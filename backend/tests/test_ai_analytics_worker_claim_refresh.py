"""Unit tests for ai_analytics_worker.claim_refresh (Phase 5).

Feature under test: the shared single-claim refresh algorithm that fetches
source data, builds a projection, and persists it. Used by both the
historical backfill (Phase 4) and the change-stream listener (Phase 5).

Failure prevented:
- A claim refresh that raises instead of returning a result would kill the
  backfill or change-stream listener. The refresh must dead-letter and return.
- A claim refresh that swallows ``asyncio.CancelledError`` would prevent
  graceful shutdown. Cancellation must propagate.

Test level: unit. Uses the in-memory mongomock backend from conftest for
destination writes, and mocks the source_repository calls.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from pymongo.errors import ConnectionFailure

from ai_analytics_worker.claim_refresh import (
    ClaimRefreshResult,
    OUTCOME_DEAD_LETTERED,
    OUTCOME_INSERTED,
    OUTCOME_NO_SOURCE,
    OUTCOME_UPDATED,
    refresh_claim,
)
from ai_analytics_worker.config import worker_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_full_doc(claim_id: int = 12345, **overrides):
    """Build a full ai_line_items doc that build_projection can consume."""
    base = {
        "_id": ObjectId(),
        "claim_id": claim_id,
        "department_id": 42,
        "department_name": "Fire Department",
        "updated_at": datetime(2026, 7, 2, 10, 30, 0),
        "claim_processing_status": "COMPLETED",
        "agent_exec_status": "success",
        "confidence_level": 85,
        "billing_category": "Fire Suppression",
        "line_items_save_to_rh_status": True,
        "retry_count": 0,
        "line_items": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestRefreshClaimHappyPath:
    """Tests that a normal refresh fetches, builds, and persists."""

    @pytest.mark.asyncio
    async def test_inserts_new_projection(self, mock_mongo_db):
        """A claim with no existing projection is inserted."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(return_value=make_full_doc(claim_id=100)),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            result = await refresh_claim(ai_db, db, 100, "insert")

        assert result.outcome == OUTCOME_INSERTED
        assert result.claim_id == 100
        assert result.error_type is None
        assert result.error_message is None

        count = await db[worker_config.PROJECTIONS_COLLECTION].count_documents(
            {"_id": 100}
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_updates_existing_projection(self, mock_mongo_db):
        """A claim with an existing projection is updated."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        # Seed an existing projection
        await db[worker_config.PROJECTIONS_COLLECTION].insert_one(
            {"_id": 100, "department_name": "Old Name"}
        )

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(return_value=make_full_doc(claim_id=100, department_name="New Name")),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            result = await refresh_claim(ai_db, db, 100, "update")

        assert result.outcome == OUTCOME_UPDATED
        assert result.claim_id == 100

        doc = await db[worker_config.PROJECTIONS_COLLECTION].find_one({"_id": 100})
        assert doc["department_name"] == "New Name"

    @pytest.mark.asyncio
    async def test_source_event_type_recorded_in_dead_letter(self, mock_mongo_db):
        """The source_event_type is passed through to dead-letter records."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(side_effect=ConnectionFailure("connection dropped")),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            result = await refresh_claim(ai_db, db, 100, "replace")

        assert result.outcome == OUTCOME_DEAD_LETTERED
        dl = await db[worker_config.DEAD_LETTERS_COLLECTION].find_one(
            {"claim_id": 100}
        )
        assert dl is not None
        assert dl["source_event_type"] == "replace"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestRefreshClaimErrorHandling:
    """Tests that errors are dead-lettered and returned, not raised."""

    @pytest.mark.asyncio
    async def test_transient_error_dead_lettered(self, mock_mongo_db):
        """A ConnectionFailure from source fetch is dead-lettered."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(side_effect=ConnectionFailure("connection dropped")),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            result = await refresh_claim(ai_db, db, 100, "insert")

        assert result.outcome == OUTCOME_DEAD_LETTERED
        assert result.claim_id == 100
        assert result.error_type == "ConnectionFailure"
        assert "connection dropped" in result.error_message

        dl = await db[worker_config.DEAD_LETTERS_COLLECTION].find_one(
            {"claim_id": 100}
        )
        assert dl is not None
        assert dl["error_type"] == "ConnectionFailure"

    @pytest.mark.asyncio
    async def test_generic_exception_dead_lettered(self, mock_mongo_db):
        """A non-transient error is also dead-lettered, not raised."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(side_effect=ValueError("bad data")),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            result = await refresh_claim(ai_db, db, 100, "update")

        assert result.outcome == OUTCOME_DEAD_LETTERED
        assert result.error_type == "ValueError"
        assert result.error_message == "bad data"

    @pytest.mark.asyncio
    async def test_no_source_data_dead_lettered(self, mock_mongo_db):
        """When build_projection returns None (no claim_id and no source), it's dead-lettered.

        build_projection returns None only when both claim_id is None AND
        ai_line_items is None — there's nothing to project. This is a
        defensive guard; in practice the change-stream listener skips
        events with no claim_id before calling refresh_claim.
        """
        ai_db = mock_mongo_db
        db = mock_mongo_db

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(return_value=None),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            result = await refresh_claim(ai_db, db, None, "backfill")

        assert result.outcome == OUTCOME_NO_SOURCE
        assert result.claim_id is None
        assert result.error_type == "NoSourceData"

        dl = await db[worker_config.DEAD_LETTERS_COLLECTION].find_one(
            {"claim_id": None}
        )
        assert dl is not None
        assert dl["error_type"] == "NoSourceData"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self, mock_mongo_db):
        """asyncio.CancelledError propagates without dead-lettering."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        import asyncio

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            with pytest.raises(asyncio.CancelledError):
                await refresh_claim(ai_db, db, 100, "insert")

        # No dead-letter should be written for cancellation
        count = await db[worker_config.DEAD_LETTERS_COLLECTION].count_documents({})
        assert count == 0

    @pytest.mark.asyncio
    async def test_error_in_conversations_fetch_dead_lettered(self, mock_mongo_db):
        """An error in the second fetch (conversations) is also dead-lettered."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=AsyncMock(return_value=make_full_doc(claim_id=100)),
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(side_effect=RuntimeError("conversations failed")),
        ):
            result = await refresh_claim(ai_db, db, 100, "insert")

        assert result.outcome == OUTCOME_DEAD_LETTERED
        assert result.error_type == "RuntimeError"
        assert result.error_message == "conversations failed"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestRefreshClaimIdempotency:
    """Tests that refreshing the same claim twice is idempotent."""

    @pytest.mark.asyncio
    async def test_double_refresh_inserts_then_updates(self, mock_mongo_db):
        """First refresh inserts, second refresh updates the same projection."""
        ai_db = mock_mongo_db
        db = mock_mongo_db

        mock_fetch = AsyncMock(return_value=make_full_doc(claim_id=100))
        with patch(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            new=mock_fetch,
        ), patch(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            new=AsyncMock(return_value=[]),
        ):
            r1 = await refresh_claim(ai_db, db, 100, "backfill")
            r2 = await refresh_claim(ai_db, db, 100, "backfill")

        assert r1.outcome == OUTCOME_INSERTED
        assert r2.outcome == OUTCOME_UPDATED

        count = await db[worker_config.PROJECTIONS_COLLECTION].count_documents(
            {"_id": 100}
        )
        assert count == 1


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


class TestClaimRefreshResult:
    """Tests for the ClaimRefreshResult dataclass."""

    def test_result_defaults(self):
        """Result with only required fields has None defaults for errors."""
        result = ClaimRefreshResult(
            outcome=OUTCOME_INSERTED,
            claim_id=100,
        )
        assert result.error_type is None
        assert result.error_message is None

    def test_result_with_error(self):
        """Result with error fields stores them correctly."""
        result = ClaimRefreshResult(
            outcome=OUTCOME_DEAD_LETTERED,
            claim_id=100,
            error_type="ConnectionFailure",
            error_message="connection dropped",
        )
        assert result.outcome == OUTCOME_DEAD_LETTERED
        assert result.error_type == "ConnectionFailure"
