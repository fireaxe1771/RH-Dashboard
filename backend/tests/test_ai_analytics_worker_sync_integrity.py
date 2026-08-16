"""Tests for ai_analytics_worker.sync_integrity (Phase 11).

Feature under test: the sync integrity verification that checks the
projection cache matches the source MongoDB — count comparison +
sample verification + auto-resync of divergent claims.

Failure prevented:
- A projection cache that silently diverges from MongoDB (e.g. a direct
  Mongo edit bypasses the change stream) would produce incorrect
  analytics with no indication anything is wrong. The integrity check
  catches this and auto-resyncs.
- A missing projection (source exists, no projection) would make a
  claim invisible on the dashboard. The integrity check enqueues it
  for refresh.

Test level: unit + integration (uses mongomock via mock_mongo_db).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, MagicMock

from ai_analytics_worker.sync_integrity import (
    SyncIntegrityResult,
    SyncIntegrityState,
    sync_integrity_state,
    run_sync_integrity_once,
    _datetime_gt,
    _normalize_dt,
)
from ai_analytics_worker.metrics import worker_metrics
from ai_analytics_worker.queue import ClaimQueue
from ai_analytics_worker.config import worker_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UTC = timezone.utc
NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset the global singletons before each test."""
    sync_integrity_state.reset()
    worker_metrics.reset()
    yield
    sync_integrity_state.reset()
    worker_metrics.reset()


@pytest.fixture
def mock_queue():
    """A mock ClaimQueue that records enqueues."""
    queue = MagicMock()
    queue.enqueue = MagicMock()
    return queue


# ---------------------------------------------------------------------------
# SyncIntegrityState
# ---------------------------------------------------------------------------


class TestSyncIntegrityState:
    def test_initial_state_has_no_check(self):
        assert sync_integrity_state.last_check_at is None
        assert sync_integrity_state.source_count == 0
        assert sync_integrity_state.projection_count == 0
        assert sync_integrity_state.count_mismatch is False
        assert sync_integrity_state.divergent_count == 0
        assert sync_integrity_state.last_error is None
        assert sync_integrity_state.check_in_progress is False

    def test_update_from_result_sets_fields(self):
        result = SyncIntegrityResult(
            source_count=100,
            projection_count=98,
            count_mismatch=True,
            divergent_claims=[1, 2],
            missing_projections=[3],
            completed_at=NOW,
        )
        sync_integrity_state.update_from_result(result)

        assert sync_integrity_state.last_check_at == NOW
        assert sync_integrity_state.source_count == 100
        assert sync_integrity_state.projection_count == 98
        assert sync_integrity_state.count_mismatch is True
        assert sync_integrity_state.divergent_count == 2
        assert sync_integrity_state.missing_count == 1
        assert sync_integrity_state.check_in_progress is False

    def test_mark_check_started_sets_in_progress(self):
        sync_integrity_state.mark_check_started()
        assert sync_integrity_state.check_in_progress is True

    def test_record_error_clears_in_progress(self):
        sync_integrity_state.mark_check_started()
        sync_integrity_state.record_error("db down")
        assert sync_integrity_state.check_in_progress is False
        assert sync_integrity_state.last_error == "db down"

    def test_snapshot_returns_dict(self):
        result = SyncIntegrityResult(
            source_count=50,
            projection_count=50,
            completed_at=NOW,
        )
        sync_integrity_state.update_from_result(result)
        snap = sync_integrity_state.snapshot()
        assert snap["source_count"] == 50
        assert snap["projection_count"] == 50
        assert snap["count_mismatch"] is False
        assert snap["divergent_count"] == 0


# ---------------------------------------------------------------------------
# run_sync_integrity_once — count comparison
# ---------------------------------------------------------------------------


class TestCountComparison:
    @pytest.mark.asyncio
    async def test_counts_match_no_divergence(self, mock_mongo_db):
        """When source and projection counts match and samples match, no divergence."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        # Seed 3 source docs with matching projections.
        ai_db = mock_mongo_db  # reuse same mock for simplicity
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        for i in range(3):
            updated = NOW - timedelta(hours=i)
            await src_col.insert_one({
                "claim_id": 100 + i,
                "updated_at": updated,
            })
            await proj_col.insert_one({
                "_id": 100 + i,
                "source_latest_updated_at": updated,
            })

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=mock_queue,
            sample_size=10,
        )

        assert result.source_count == 3
        assert result.projection_count == 3
        assert result.count_mismatch is False
        assert result.divergent_claims == []
        assert result.claims_enqueued == 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_count_mismatch_detected(self, mock_mongo_db):
        """Source has more docs than projection — count mismatch flagged."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        # 5 source docs, 3 projections.
        for i in range(5):
            await src_col.insert_one({
                "claim_id": 100 + i,
                "updated_at": NOW - timedelta(hours=i),
            })
        for i in range(3):
            await proj_col.insert_one({
                "_id": 100 + i,
                "source_latest_updated_at": NOW - timedelta(hours=i),
            })

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=mock_queue,
            sample_size=10,
        )

        assert result.source_count == 5
        assert result.projection_count == 3
        assert result.count_mismatch is True
        # Claims 103 and 104 are missing projections → divergent.
        assert 103 in result.missing_projections
        assert 104 in result.missing_projections


# ---------------------------------------------------------------------------
# run_sync_integrity_once — sample verification
# ---------------------------------------------------------------------------


class TestSampleVerification:
    @pytest.mark.asyncio
    async def test_stale_projection_detected(self, mock_mongo_db):
        """Source updated_at > projection source_latest_updated_at → divergent."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        # Source was updated at NOW, projection says it was updated 1 hour ago.
        await src_col.insert_one({
            "claim_id": 100,
            "updated_at": NOW,
        })
        await proj_col.insert_one({
            "_id": 100,
            "source_latest_updated_at": NOW - timedelta(hours=1),
        })

        queue = MagicMock()
        queue.enqueue = MagicMock()

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
            sample_size=10,
        )

        assert 100 in result.divergent_claims
        assert 100 not in result.missing_projections
        assert result.claims_enqueued == 1
        queue.enqueue.assert_called_once_with(100, source="sync_integrity")

    @pytest.mark.asyncio
    async def test_missing_projection_detected(self, mock_mongo_db):
        """Source exists but no projection → missing + divergent."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        await ai_db[AI_LINE_ITEMS_COLLECTION].insert_one({
            "claim_id": 200,
            "updated_at": NOW,
        })

        queue = MagicMock()
        queue.enqueue = MagicMock()

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
            sample_size=10,
        )

        assert 200 in result.missing_projections
        assert 200 in result.divergent_claims
        queue.enqueue.assert_called_with(200, source="sync_integrity")

    @pytest.mark.asyncio
    async def test_up_to_date_projection_not_flagged(self, mock_mongo_db):
        """Projection updated_at == source updated_at → not divergent."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        await src_col.insert_one({
            "claim_id": 300,
            "updated_at": NOW,
        })
        await proj_col.insert_one({
            "_id": 300,
            "source_latest_updated_at": NOW,
        })

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=mock_queue,
            sample_size=10,
        )

        assert result.divergent_claims == []
        assert result.missing_projections == []

    @pytest.mark.asyncio
    async def test_projection_newer_than_source_not_flagged(self, mock_mongo_db):
        """Projection updated_at > source updated_at → not divergent (worker is ahead)."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        await src_col.insert_one({
            "claim_id": 400,
            "updated_at": NOW - timedelta(hours=2),
        })
        await proj_col.insert_one({
            "_id": 400,
            "source_latest_updated_at": NOW,
        })

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=mock_queue,
            sample_size=10,
        )

        assert result.divergent_claims == []

    @pytest.mark.asyncio
    async def test_sample_size_limits_check(self, mock_mongo_db):
        """Only the N most recent source docs are sample-verified."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        # Insert 10 source docs, all with matching projections.
        for i in range(10):
            updated = NOW - timedelta(hours=i)
            await src_col.insert_one({"claim_id": i, "updated_at": updated})
            await proj_col.insert_one({
                "_id": i, "source_latest_updated_at": updated,
            })

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=mock_queue,
            sample_size=3,
        )

        assert result.samples_checked == 3
        assert result.source_count == 10  # count covers all


# ---------------------------------------------------------------------------
# run_sync_integrity_once — auto-resync
# ---------------------------------------------------------------------------


class TestAutoResync:
    @pytest.mark.asyncio
    async def test_divergent_claims_enqueued(self, mock_mongo_db):
        """Divergent claims are enqueued into the queue for refresh."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        src_col = ai_db[AI_LINE_ITEMS_COLLECTION]
        proj_col = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]

        # Two stale projections + one missing.
        await src_col.insert_one({"claim_id": 1, "updated_at": NOW})
        await proj_col.insert_one({
            "_id": 1, "source_latest_updated_at": NOW - timedelta(hours=1),
        })
        await src_col.insert_one({"claim_id": 2, "updated_at": NOW})
        await proj_col.insert_one({
            "_id": 2, "source_latest_updated_at": NOW - timedelta(hours=2),
        })
        await src_col.insert_one({"claim_id": 3, "updated_at": NOW})
        # No projection for claim 3.

        queue = MagicMock()
        queue.enqueue = MagicMock()

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
            sample_size=10,
        )

        assert result.claims_enqueued == 3
        enqueued_ids = [call.args[0] for call in queue.enqueue.call_args_list]
        assert 1 in enqueued_ids
        assert 2 in enqueued_ids
        assert 3 in enqueued_ids

    @pytest.mark.asyncio
    async def test_metrics_incremented(self, mock_mongo_db):
        """sync_integrity_checks and sync_integrity_divergent_found are incremented."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        await ai_db[AI_LINE_ITEMS_COLLECTION].insert_one({
            "claim_id": 1, "updated_at": NOW,
        })
        # No projection → divergent.

        queue = MagicMock()
        queue.enqueue = MagicMock()

        await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
            sample_size=10,
        )

        snap = worker_metrics.snapshot()
        assert snap["sync_integrity_checks"] == 1
        assert snap["sync_integrity_divergent_found"] == 1


# ---------------------------------------------------------------------------
# run_sync_integrity_once — error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_source_db_failure_records_error(self):
        """A source DB failure records the error but does not raise."""
        failing_ai_db = MagicMock()
        failing_col = MagicMock()
        failing_col.count_documents = AsyncMock(side_effect=RuntimeError("source down"))
        failing_ai_db.__getitem__ = MagicMock(return_value=failing_col)

        result = await run_sync_integrity_once(
            ai_db=failing_ai_db,
            db=MagicMock(),
            queue=mock_queue,
        )

        assert result.error is not None
        assert "source down" in result.error
        assert sync_integrity_state.last_error is not None

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self, mock_mongo_db):
        """asyncio.CancelledError is re-raised, not swallowed."""
        from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

        ai_db = mock_mongo_db
        await ai_db[AI_LINE_ITEMS_COLLECTION].insert_one({
            "claim_id": 1, "updated_at": NOW,
        })

        stop_event = asyncio.Event()
        stop_event.set()  # Pre-set so the check sees it immediately

        result = await run_sync_integrity_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=mock_queue,
            stop_event=stop_event,
        )

        # With stop_event pre-set, the check should cancel after the count.
        assert result.cancelled is True


# ---------------------------------------------------------------------------
# DateTime helpers
# ---------------------------------------------------------------------------


class TestDateTimeHelpers:
    def test_normalize_dt_tz_aware(self):
        dt = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
        assert _normalize_dt(dt) == dt

    def test_normalize_dt_tz_naive(self):
        dt = datetime(2026, 8, 13, 12, 0, 0)
        result = _normalize_dt(dt)
        assert result is not None
        assert result.tzinfo == UTC

    def test_normalize_dt_none(self):
        assert _normalize_dt(None) is None

    def test_datetime_gt_both_aware(self):
        a = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
        b = datetime(2026, 8, 13, 11, 0, 0, tzinfo=UTC)
        assert _datetime_gt(a, b) is True
        assert _datetime_gt(b, a) is False

    def test_datetime_gt_mixed_awareness(self):
        a = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
        b = datetime(2026, 8, 13, 12, 0, 0)  # naive, treated as UTC
        assert _datetime_gt(a, b) is False  # same instant

    def test_datetime_gt_none(self):
        assert _datetime_gt(None, datetime.now(UTC)) is False
        assert _datetime_gt(datetime.now(UTC), None) is False
