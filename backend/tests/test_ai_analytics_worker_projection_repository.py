"""Unit tests for ai_analytics_worker.projection_repository (Phase 4).

Feature under test: the persistence layer that writes projections, worker
run audit records, worker state, and dead-letter records to the
dashboard-owned MongoDB database.

Failure prevented:
- A projection with ``_id=None`` silently inserted with an auto-generated
  ObjectId — the projection would then be uncorrelatable with its claim.
- A duplicate worker run record left in an inconsistent state after a
  partial failure.
- A dead-letter record clobbering an existing unresolved dead-letter (the
  attempt count and timestamps must be preserved/updated).

Test level: unit. Uses the in-memory mongomock backend from conftest.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from ai_analytics_worker.config import worker_config
from ai_analytics_worker.projection_repository import (
    get_worker_state,
    record_dead_letter,
    record_worker_run,
    update_worker_run,
    update_worker_state,
    upsert_projection,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_projection(claim_id: int = 12345, **overrides):
    """Build a minimal valid projection dict for testing."""
    base = {
        "_id": claim_id,
        "claim_id": claim_id,
        "department_id": 42,
        "department_name": "Fire Department",
        "worker_version": "0.1.0",
        "projection_schema_version": 1,
        "data_quality_flags": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# upsert_projection
# ---------------------------------------------------------------------------


class TestUpsertProjection:
    """Tests for upsert_projection — the core persistence operation."""

    @pytest.mark.asyncio
    async def test_inserts_new_projection(self, mock_mongo_db):
        proj = make_projection(claim_id=12345)
        result = await upsert_projection(mock_mongo_db, proj)

        assert result == "inserted"
        # Verify it was actually written
        doc = await mock_mongo_db[worker_config.PROJECTIONS_COLLECTION].find_one(
            {"_id": 12345}
        )
        assert doc is not None
        assert doc["claim_id"] == 12345
        assert doc["department_name"] == "Fire Department"

    @pytest.mark.asyncio
    async def test_updates_existing_projection(self, mock_mongo_db):
        # Insert first
        proj1 = make_projection(claim_id=12345, department_name="Fire Dept")
        await upsert_projection(mock_mongo_db, proj1)

        # Upsert again with different data
        proj2 = make_projection(claim_id=12345, department_name="Rescue Squad")
        result = await upsert_projection(mock_mongo_db, proj2)

        assert result == "updated"
        doc = await mock_mongo_db[worker_config.PROJECTIONS_COLLECTION].find_one(
            {"_id": 12345}
        )
        assert doc["department_name"] == "Rescue Squad"

    @pytest.mark.asyncio
    async def test_upsert_replaces_entire_document(self, mock_mongo_db):
        """replace_one ensures stale fields from old schema are removed."""
        # Insert with an extra field that won't be in the replacement
        proj1 = make_projection(claim_id=12345, old_stale_field="remove me")
        await upsert_projection(mock_mongo_db, proj1)

        # Upsert without the stale field
        proj2 = make_projection(claim_id=12345)
        proj2.pop("old_stale_field", None)
        await upsert_projection(mock_mongo_db, proj2)

        doc = await mock_mongo_db[worker_config.PROJECTIONS_COLLECTION].find_one(
            {"_id": 12345}
        )
        assert "old_stale_field" not in doc

    @pytest.mark.asyncio
    async def test_raises_value_error_for_none_id(self, mock_mongo_db):
        proj = make_projection()
        proj["_id"] = None
        with pytest.raises(ValueError, match="_id=None"):
            await upsert_projection(mock_mongo_db, proj)

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent(self, mock_mongo_db):
        """Running the same upsert twice produces one document, not two."""
        proj = make_projection(claim_id=12345)
        await upsert_projection(mock_mongo_db, proj)
        await upsert_projection(mock_mongo_db, proj)

        count = await mock_mongo_db[
            worker_config.PROJECTIONS_COLLECTION
        ].count_documents({"_id": 12345})
        assert count == 1


# ---------------------------------------------------------------------------
# record_worker_run / update_worker_run
# ---------------------------------------------------------------------------


class TestWorkerRuns:
    """Tests for the worker run audit log."""

    @pytest.mark.asyncio
    async def test_records_new_run(self, mock_mongo_db):
        run_doc = {
            "run_type": "backfill",
            "started_at": datetime(2026, 8, 13, 12, 0, 0),
            "status": "running",
            "worker_version": "0.1.0",
        }
        run_id = await record_worker_run(mock_mongo_db, run_doc)

        assert isinstance(run_id, ObjectId)
        doc = await mock_mongo_db[
            worker_config.WORKER_RUNS_COLLECTION
        ].find_one({"_id": run_id})
        assert doc is not None
        assert doc["run_type"] == "backfill"
        assert doc["status"] == "running"

    @pytest.mark.asyncio
    async def test_updates_run_with_completion_stats(self, mock_mongo_db):
        run_doc = {
            "run_type": "backfill",
            "started_at": datetime(2026, 8, 13, 12, 0, 0),
            "status": "running",
            "worker_version": "0.1.0",
        }
        run_id = await record_worker_run(mock_mongo_db, run_doc)

        await update_worker_run(
            mock_mongo_db,
            run_id,
            {
                "completed_at": datetime(2026, 8, 13, 12, 5, 0),
                "status": "completed",
                "claims_processed": 100,
                "claims_failed": 2,
            },
        )

        doc = await mock_mongo_db[
            worker_config.WORKER_RUNS_COLLECTION
        ].find_one({"_id": run_id})
        assert doc["status"] == "completed"
        assert doc["claims_processed"] == 100
        assert doc["claims_failed"] == 2
        assert doc["completed_at"] == datetime(2026, 8, 13, 12, 5, 0)


# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------


class TestWorkerState:
    """Tests for the worker state document CRUD."""

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_state(self, mock_mongo_db):
        result = await get_worker_state(
            mock_mongo_db, worker_config.WORKER_NAME
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_creates_state_if_not_exists(self, mock_mongo_db):
        await update_worker_state(
            mock_mongo_db,
            worker_config.WORKER_NAME,
            {"status": "running", "last_started_at": datetime(2026, 8, 13)},
        )

        doc = await get_worker_state(mock_mongo_db, worker_config.WORKER_NAME)
        assert doc is not None
        assert doc["status"] == "running"
        assert doc["last_started_at"] == datetime(2026, 8, 13)

    @pytest.mark.asyncio
    async def test_update_preserves_existing_fields(self, mock_mongo_db):
        """$set updates specific fields without clobbering others."""
        await update_worker_state(
            mock_mongo_db,
            worker_config.WORKER_NAME,
            {"status": "running", "resume_token": {"_data": "abc"}},
        )
        await update_worker_state(
            mock_mongo_db,
            worker_config.WORKER_NAME,
            {"status": "stopped"},
        )

        doc = await get_worker_state(mock_mongo_db, worker_config.WORKER_NAME)
        assert doc["status"] == "stopped"
        # resume_token must still be there — $set doesn't remove it
        assert doc["resume_token"] == {"_data": "abc"}


# ---------------------------------------------------------------------------
# Dead-letter
# ---------------------------------------------------------------------------


class TestDeadLetter:
    """Tests for dead-letter record creation and update."""

    @pytest.mark.asyncio
    async def test_records_new_dead_letter(self, mock_mongo_db):
        dl_id = await record_dead_letter(
            mock_mongo_db,
            claim_id=12345,
            source_event_type="backfill",
            error_type="ConnectionFailure",
            error_message="connection dropped",
            attempt_count=3,
        )

        assert isinstance(dl_id, ObjectId)
        doc = await mock_mongo_db[
            worker_config.DEAD_LETTERS_COLLECTION
        ].find_one({"_id": dl_id})
        assert doc is not None
        assert doc["claim_id"] == 12345
        assert doc["error_type"] == "ConnectionFailure"
        assert doc["attempt_count"] == 3
        assert doc["resolved"] is False
        assert doc["first_failed_at"] is not None
        assert doc["last_failed_at"] is not None

    @pytest.mark.asyncio
    async def test_updates_existing_unresolved_dead_letter(self, mock_mongo_db):
        """Re-dead-lettering the same claim updates, not duplicates."""
        await record_dead_letter(
            mock_mongo_db,
            claim_id=12345,
            source_event_type="backfill",
            error_type="ConnectionFailure",
            error_message="first failure",
            attempt_count=1,
        )
        await record_dead_letter(
            mock_mongo_db,
            claim_id=12345,
            source_event_type="backfill",
            error_type="NetworkTimeout",
            error_message="second failure",
            attempt_count=2,
        )

        count = await mock_mongo_db[
            worker_config.DEAD_LETTERS_COLLECTION
        ].count_documents({"claim_id": 12345})
        assert count == 1

        doc = await mock_mongo_db[
            worker_config.DEAD_LETTERS_COLLECTION
        ].find_one({"claim_id": 12345})
        assert doc["error_type"] == "NetworkTimeout"
        assert doc["attempt_count"] == 2
        assert doc["error_message"] == "second failure"

    @pytest.mark.asyncio
    async def test_creates_new_dead_letter_if_existing_resolved(self, mock_mongo_db):
        """If the prior dead-letter was resolved, a new one is created."""
        first_id = await record_dead_letter(
            mock_mongo_db,
            claim_id=12345,
            source_event_type="backfill",
            error_type="ConnectionFailure",
            error_message="first failure",
        )
        # Manually mark as resolved
        await mock_mongo_db[worker_config.DEAD_LETTERS_COLLECTION].update_one(
            {"_id": first_id},
            {"$set": {"resolved": True}},
        )
        # Now dead-letter again
        second_id = await record_dead_letter(
            mock_mongo_db,
            claim_id=12345,
            source_event_type="backfill",
            error_type="NetworkTimeout",
            error_message="second failure after resolution",
        )

        assert first_id != second_id
        count = await mock_mongo_db[
            worker_config.DEAD_LETTERS_COLLECTION
        ].count_documents({"claim_id": 12345})
        assert count == 2

    @pytest.mark.asyncio
    async def test_dead_letter_with_none_claim_id(self, mock_mongo_db):
        """claim_id=None always inserts (no update path for None claim_id)."""
        dl_id = await record_dead_letter(
            mock_mongo_db,
            claim_id=None,
            source_event_type="backfill",
            error_type="ValueError",
            error_message="could not extract claim_id",
        )

        assert isinstance(dl_id, ObjectId)
        doc = await mock_mongo_db[
            worker_config.DEAD_LETTERS_COLLECTION
        ].find_one({"_id": dl_id})
        assert doc["claim_id"] is None
        assert doc["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_uses_worker_config_version_by_default(self, mock_mongo_db):
        dl_id = await record_dead_letter(
            mock_mongo_db,
            claim_id=12345,
            source_event_type="backfill",
            error_type="ConnectionFailure",
            error_message="failure",
        )

        doc = await mock_mongo_db[
            worker_config.DEAD_LETTERS_COLLECTION
        ].find_one({"_id": dl_id})
        assert doc["worker_version"] == worker_config.worker_version
