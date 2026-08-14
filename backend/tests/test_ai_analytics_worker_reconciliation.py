"""Unit tests for ai_analytics_worker.reconciliation (Phase 7).

Feature under test: the safety-net reconciliation that periodically scans
``ai_line_items`` for claims updated since the last checkpoint and enqueues
them into the ClaimQueue for refresh.

Failure prevented:
-- Events missed by the change stream (oplog gap, crash between enqueue and
   refresh) would leave stale projections. Reconciliation catches them by
   scanning ``updated_at > watermark``.
-- A reconciliation scan that crashes would silently stop the safety net.
   The loop logs and continues on the next interval.

Test level: unit. Uses mongomock via the mock_mongo_db fixture.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from ai_analytics_worker.config import worker_config
from ai_analytics_worker.queue import ClaimQueue
from ai_analytics_worker.reconciliation import (
    _RECONCILIATION_SAFETY_MARGIN_MINUTES,
    ReconciliationResult,
    run_reconciliation_loop,
    run_reconciliation_once,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_checkpoint(db, checkpoint: datetime) -> None:
    """Seed worker state with a last_checkpoint_at."""
    await db[worker_config.WORKER_STATE_COLLECTION].update_one(
        {"_id": worker_config.WORKER_NAME},
        {"$set": {"last_checkpoint_at": checkpoint}},
        upsert=True,
    )


def _make_ai_line_item(claim_id: int, updated_at: datetime, _id: str = None):
    """Build a minimal ai_line_items document for the scan."""
    return {
        "_id": _id or f"doc_{claim_id}",
        "claim_id": claim_id,
        "updated_at": updated_at,
    }


class FakeAIDB:
    """A fake ai_db with a configurable find() cursor for reconciliation scans."""

    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, name):
        return self._collection


class FakeAsyncCursor:
    """A fake async cursor that returns pre-seeded documents in batches."""

    def __init__(self, documents, batch_size=100):
        self._docs = list(documents)
        self._batch_size = batch_size
        self._sort_key = None

    def sort(self, field, direction):
        self._sort_key = field
        self._docs.sort(key=lambda d: d.get(field, ""))
        return self

    def batch_size(self, size):
        self._batch_size = size
        return self

    async def to_list(self, length=None):
        n = length if length is not None else self._batch_size
        batch = self._docs[:n]
        self._docs = self._docs[n:]
        return batch


class FakeAsyncCollection:
    """A fake collection that returns FakeAsyncCursor from find()."""

    def __init__(self, documents):
        self._documents = list(documents)

    def find(self, query=None, projection=None):
        # Simple filter: support {"updated_at": {"$gt": val}}
        docs = self._documents
        if query and "updated_at" in query:
            gt_val = query["updated_at"]["$gt"]
            docs = [d for d in docs if d.get("updated_at") and d["updated_at"] > gt_val]
        return FakeAsyncCursor(docs)


# ---------------------------------------------------------------------------
# run_reconciliation_once
# ---------------------------------------------------------------------------


class TestReconciliationOnce:
    """Tests for a single reconciliation scan."""

    @pytest.mark.asyncio
    async def test_skips_when_no_checkpoint(self, mock_mongo_db):
        """Reconciliation is skipped if last_checkpoint_at is None."""
        queue = ClaimQueue(debounce_seconds=0.0)
        fake_collection = FakeAsyncCollection([])
        ai_db = FakeAIDB(fake_collection)

        result = await run_reconciliation_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
        )

        assert result.claims_found == 0
        assert result.claims_enqueued == 0
        assert result.watermark is None
        assert result.completed_at is not None
        # Run record should show "skipped" status
        runs = await mock_mongo_db[
            worker_config.WORKER_RUNS_COLLECTION
        ].find_one({"run_type": "reconciliation"})
        assert runs is not None
        assert runs["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_scans_and_enqueues_claims_after_checkpoint(
        self, mock_mongo_db
    ):
        """Claims with updated_at > watermark are enqueued."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        # Three claims: two after checkpoint, one before
        docs = [
            _make_ai_line_item(100, now - timedelta(minutes=30)),
            _make_ai_line_item(200, now - timedelta(minutes=10)),
            _make_ai_line_item(300, now - timedelta(hours=3)),  # Before checkpoint
        ]
        fake_collection = FakeAsyncCollection(docs)
        ai_db = FakeAIDB(fake_collection)

        queue = ClaimQueue(debounce_seconds=0.0)
        result = await run_reconciliation_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
        )

        # Claim 300 is before the checkpoint, but the safety margin subtracts
        # 5 minutes from the checkpoint, so the watermark is checkpoint - 5m.
        # Claim 300 (3 hours before checkpoint) is still before the watermark,
        # so it should NOT be found.
        assert result.claims_found == 2  # 100 and 200
        assert result.claims_enqueued == 2
        assert result.claims_skipped == 0
        assert result.watermark is not None
        # The watermark should be checkpoint - safety_margin. Mongomock
        # may truncate microseconds, so compare with a 1-second tolerance.
        expected_watermark = checkpoint - timedelta(
            minutes=_RECONCILIATION_SAFETY_MARGIN_MINUTES
        )
        diff = abs((result.watermark - expected_watermark).total_seconds())
        assert diff < 1.0

    @pytest.mark.asyncio
    async def test_deduplicates_claim_ids(self, mock_mongo_db):
        """Multiple ai_line_items with the same claim_id are enqueued once."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        docs = [
            _make_ai_line_item(100, now - timedelta(minutes=30), _id="doc_1"),
            _make_ai_line_item(100, now - timedelta(minutes=20), _id="doc_2"),
            _make_ai_line_item(100, now - timedelta(minutes=10), _id="doc_3"),
        ]
        fake_collection = FakeAsyncCollection(docs)
        ai_db = FakeAIDB(fake_collection)

        queue = ClaimQueue(debounce_seconds=0.0)
        result = await run_reconciliation_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
        )

        assert result.claims_found == 3  # 3 documents found
        assert result.claims_enqueued == 1  # But only 1 distinct claim_id
        assert queue.size == 1

    @pytest.mark.asyncio
    async def test_skips_invalid_claim_ids(self, mock_mongo_db):
        """Documents with missing or non-numeric claim_id are skipped."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        docs = [
            _make_ai_line_item(100, now - timedelta(minutes=30)),
            {"_id": "doc_2", "claim_id": None, "updated_at": now},
            {"_id": "doc_3", "claim_id": "not-a-number", "updated_at": now},
            {"_id": "doc_4", "updated_at": now},  # Missing claim_id
        ]
        fake_collection = FakeAsyncCollection(docs)
        ai_db = FakeAIDB(fake_collection)

        queue = ClaimQueue(debounce_seconds=0.0)
        result = await run_reconciliation_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
        )

        assert result.claims_found == 4
        assert result.claims_enqueued == 1  # Only claim 100
        assert result.claims_skipped == 3

    @pytest.mark.asyncio
    async def test_respects_stop_event(self, mock_mongo_db):
        """The scan stops when stop_event is set between batches."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        docs = [_make_ai_line_item(i, now) for i in range(100)]
        fake_collection = FakeAsyncCollection(docs)
        ai_db = FakeAIDB(fake_collection)

        stop_event = asyncio.Event()
        stop_event.set()  # Already set — scan should detect it

        queue = ClaimQueue(debounce_seconds=0.0)
        result = await run_reconciliation_once(
            ai_db=ai_db,
            db=mock_mongo_db,
            queue=queue,
            stop_event=stop_event,
        )

        assert result.cancelled is True

    @pytest.mark.asyncio
    async def test_records_run_in_audit_log(self, mock_mongo_db):
        """A reconciliation run record is created and updated."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        docs = [_make_ai_line_item(100, now)]
        fake_collection = FakeAsyncCollection(docs)
        ai_db = FakeAIDB(fake_collection)

        queue = ClaimQueue(debounce_seconds=0.0)
        await run_reconciliation_once(ai_db=ai_db, db=mock_mongo_db, queue=queue)

        run = await mock_mongo_db[
            worker_config.WORKER_RUNS_COLLECTION
        ].find_one({"run_type": "reconciliation"})
        assert run is not None
        assert run["status"] == "completed"
        assert run["started_at"] is not None
        assert run["completed_at"] is not None
        assert run["claims_processed"] == 1


# ---------------------------------------------------------------------------
# run_reconciliation_loop
# ---------------------------------------------------------------------------


class TestReconciliationLoop:
    """Tests for the periodic reconciliation loop."""

    @pytest.mark.asyncio
    async def test_loop_runs_scan_then_sleeps(self, mock_mongo_db):
        """The loop runs a scan, then waits for the interval before the next."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        docs = [_make_ai_line_item(100, now)]
        fake_collection = FakeAsyncCollection(docs)
        ai_db = FakeAIDB(fake_collection)

        queue = ClaimQueue(debounce_seconds=0.0)
        stop_event = asyncio.Event()

        scan_count = {"n": 0}
        original_once = run_reconciliation_once

        async def counting_once(**kwargs):
            scan_count["n"] += 1
            return await original_once(**kwargs)

        with patch(
            "ai_analytics_worker.reconciliation.run_reconciliation_once",
            new=counting_once,
        ):
            task = asyncio.create_task(
                run_reconciliation_loop(
                    ai_db=ai_db,
                    db=mock_mongo_db,
                    stop_event=stop_event,
                    queue=queue,
                    interval_minutes=60,  # Long interval so only 1 scan runs
                )
            )
            await asyncio.sleep(0.3)
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        # Should have run exactly 1 scan (immediately on startup)
        assert scan_count["n"] == 1

    @pytest.mark.asyncio
    async def test_loop_continues_after_scan_error(self, mock_mongo_db):
        """A fatal scan error is logged but the loop continues."""
        now = datetime.now(UTC)
        checkpoint = now - timedelta(hours=1)

        await _seed_checkpoint(mock_mongo_db, checkpoint)

        queue = ClaimQueue(debounce_seconds=0.0)
        stop_event = asyncio.Event()

        call_count = {"n": 0}

        async def failing_once(**kwargs):
            call_count["n"] += 1
            raise RuntimeError("scan failed")

        with patch(
            "ai_analytics_worker.reconciliation.run_reconciliation_once",
            new=failing_once,
        ):
            task = asyncio.create_task(
                run_reconciliation_loop(
                    ai_db=mock_mongo_db,
                    db=mock_mongo_db,
                    stop_event=stop_event,
                    queue=queue,
                    interval_minutes=0,  # Immediate retry
                )
            )
            await asyncio.sleep(0.3)
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        # The loop should have attempted multiple scans despite errors
        assert call_count["n"] >= 2

    @pytest.mark.asyncio
    async def test_loop_stops_on_stop_event(self, mock_mongo_db):
        """The loop exits when stop_event is set during sleep."""
        queue = ClaimQueue(debounce_seconds=0.0)
        stop_event = asyncio.Event()

        with patch(
            "ai_analytics_worker.reconciliation.run_reconciliation_once",
            new=AsyncMock(),
        ):
            task = asyncio.create_task(
                run_reconciliation_loop(
                    ai_db=mock_mongo_db,
                    db=mock_mongo_db,
                    stop_event=stop_event,
                    queue=queue,
                    interval_minutes=60,
                )
            )
            await asyncio.sleep(0.1)
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
