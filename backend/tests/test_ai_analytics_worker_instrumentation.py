"""Instrumentation tests — metrics counters and health timestamps (Phase 8).

Feature under test: that the worker pipeline actually *writes* to the
``worker_metrics`` counters and the ``worker_health`` timestamps that the
Phase 8 ``/status`` and ``/ready`` endpoints expose.

Failure prevented: the counters and timestamps were defined in Phase 1 and
exposed in Phase 8, but for several phases nothing incremented them. The
existing unit tests did not catch this because
``test_ai_analytics_worker_metrics.py`` exercises the ``WorkerMetrics`` class
in isolation (which works correctly), and the route tests set counters
manually before asserting. The result was an observability surface that
reported all-zero throughput and ``null`` checkpoints forever — worse than no
endpoint, because it looks authoritative while being blind.

These tests close that gap by driving the *real* code paths and asserting the
counters move. They are deliberately written against observable counter
deltas rather than mock call assertions, so they keep passing if the
increment moves to a different line but keep failing if it disappears.

Test level: integration (pipeline + mongomock destination, mocked source).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo.errors import ConnectionFailure

from ai_analytics_worker.backfill import run_backfill
from ai_analytics_worker.change_stream_listener import (
    _process_change_event,
    _save_resume_token,
)
from ai_analytics_worker.claim_refresh import refresh_claim
from ai_analytics_worker.config import WorkerConfig, worker_config
from ai_analytics_worker.health import worker_health
from ai_analytics_worker.metrics import worker_metrics
from ai_analytics_worker.queue import ClaimQueue, run_queue_consumer
from ai_analytics_worker.reconciliation import run_reconciliation_once


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_worker_singletons():
    """Zero the metrics counters and health state before each test."""
    worker_metrics.reset()
    worker_health.reset()
    yield
    worker_metrics.reset()
    worker_health.reset()


def make_full_doc(claim_id: int = 12345, **overrides):
    """Build an ai_line_items doc that build_projection can consume."""
    base = {
        "_id": ObjectId(),
        "claim_id": claim_id,
        "department_id": 42,
        "department_name": "Fire Department",
        "updated_at": datetime(2026, 7, 2, 10, 30, 0, tzinfo=UTC),
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


def make_change_event(operation_type: str, claim_id=None):
    """Build a minimal change stream event dict."""
    event = {
        "_id": {"_data": f"token_{claim_id}_{operation_type}"},
        "operationType": operation_type,
        "ns": {"db": "AI_FEE_CALC_MULTI_AGENT_PROD", "coll": "ai_line_items"},
    }
    if operation_type != "delete":
        event["fullDocument"] = {"_id": "doc_1", "claim_id": claim_id}
    return event


def _patch_source(monkeypatch, *, line_items=None, conversations=None, error=None):
    """Patch the two source_repository fetches used by refresh_claim."""
    from unittest.mock import AsyncMock

    if error is not None:
        line_items_mock = AsyncMock(side_effect=error)
    else:
        line_items_mock = AsyncMock(return_value=line_items)
    monkeypatch.setattr(
        "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
        line_items_mock,
    )
    monkeypatch.setattr(
        "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
        AsyncMock(return_value=conversations if conversations is not None else []),
    )


# ---------------------------------------------------------------------------
# claim_refresh — the central throughput counters
# ---------------------------------------------------------------------------


class TestClaimRefreshCounters:
    """The refresh path must move claims_refreshed and the projection counters."""

    @pytest.mark.asyncio
    async def test_insert_increments_created_not_updated(
        self, mock_mongo_db, monkeypatch
    ):
        _patch_source(monkeypatch, line_items=make_full_doc(claim_id=100))

        await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "insert")

        assert worker_metrics.get("claims_refreshed") == 1
        assert worker_metrics.get("projections_created") == 1
        assert worker_metrics.get("projections_updated") == 0
        assert worker_metrics.get("claim_refresh_errors") == 0
        assert worker_metrics.get("dead_letters_created") == 0

    @pytest.mark.asyncio
    async def test_second_refresh_increments_updated(
        self, mock_mongo_db, monkeypatch
    ):
        _patch_source(monkeypatch, line_items=make_full_doc(claim_id=100))

        await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "insert")
        await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "update")

        assert worker_metrics.get("claims_refreshed") == 2
        assert worker_metrics.get("projections_created") == 1
        assert worker_metrics.get("projections_updated") == 1

    @pytest.mark.asyncio
    async def test_claims_refreshed_equals_created_plus_updated(
        self, mock_mongo_db, monkeypatch
    ):
        """The documented invariant between the three success counters.

        If this drifts, /status will show internally inconsistent numbers and
        operators cannot reason about throughput.
        """
        _patch_source(monkeypatch, line_items=make_full_doc(claim_id=100))
        for _ in range(3):
            await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "update")
        _patch_source(monkeypatch, line_items=make_full_doc(claim_id=200))
        await refresh_claim(mock_mongo_db, mock_mongo_db, 200, "insert")

        assert worker_metrics.get("claims_refreshed") == (
            worker_metrics.get("projections_created")
            + worker_metrics.get("projections_updated")
        )

    @pytest.mark.asyncio
    async def test_failure_increments_errors_and_dead_letters(
        self, mock_mongo_db, monkeypatch
    ):
        _patch_source(monkeypatch, error=ConnectionFailure("connection dropped"))

        await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "insert")

        assert worker_metrics.get("claim_refresh_errors") == 1
        assert worker_metrics.get("dead_letters_created") == 1
        # A failed refresh is not a refresh.
        assert worker_metrics.get("claims_refreshed") == 0
        assert worker_metrics.get("projections_created") == 0

    @pytest.mark.asyncio
    async def test_no_source_data_increments_dead_letters(
        self, mock_mongo_db, monkeypatch
    ):
        """build_projection returning None dead-letters and counts it."""
        _patch_source(monkeypatch, line_items=None)

        await refresh_claim(mock_mongo_db, mock_mongo_db, None, "backfill")

        assert worker_metrics.get("dead_letters_created") == 1
        assert worker_metrics.get("claims_refreshed") == 0

    @pytest.mark.asyncio
    async def test_cancellation_does_not_increment_error_counters(
        self, mock_mongo_db, monkeypatch
    ):
        """Shutdown cancellation is not a claim failure and must not be counted.

        Counting it would make every graceful deploy look like a burst of
        claim errors on the /status dashboard.
        """
        _patch_source(monkeypatch, error=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "insert")

        assert worker_metrics.get("claim_refresh_errors") == 0
        assert worker_metrics.get("dead_letters_created") == 0


# ---------------------------------------------------------------------------
# source_repository — retry counter
# ---------------------------------------------------------------------------


class TestRetryCounter:
    """Transient retries must be counted, terminal failures must not."""

    @pytest.mark.asyncio
    async def test_retry_then_success_counts_one_retry(self, monkeypatch):
        from unittest.mock import AsyncMock

        from ai_analytics_worker.source_repository import (
            get_ai_line_items_for_claim_with_retry,
        )

        monkeypatch.setattr(
            WorkerConfig, "max_retries", property(lambda self: 3)
        )
        monkeypatch.setattr(
            WorkerConfig, "source_query_timeout_ms", property(lambda self: 5000)
        )
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            AsyncMock(
                side_effect=[ConnectionFailure("dropped"), {"claim_id": 100}]
            ),
        )

        await get_ai_line_items_for_claim_with_retry(object(), 100)

        assert worker_metrics.get("claim_refresh_retries") == 1

    @pytest.mark.asyncio
    async def test_exhausted_budget_counts_retries_not_final_attempt(
        self, monkeypatch
    ):
        """With max_retries=3, three attempts means two retries.

        The third attempt is the terminal failure, surfaced as
        claim_refresh_errors by the caller — not as a retry.
        """
        from unittest.mock import AsyncMock

        from ai_analytics_worker.source_repository import (
            get_ai_line_items_for_claim_with_retry,
        )

        monkeypatch.setattr(
            WorkerConfig, "max_retries", property(lambda self: 3)
        )
        monkeypatch.setattr(
            WorkerConfig, "source_query_timeout_ms", property(lambda self: 5000)
        )
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            AsyncMock(side_effect=ConnectionFailure("always down")),
        )

        with pytest.raises(ConnectionFailure):
            await get_ai_line_items_for_claim_with_retry(object(), 100)

        assert worker_metrics.get("claim_refresh_retries") == 2

    @pytest.mark.asyncio
    async def test_non_transient_error_counts_no_retries(self, monkeypatch):
        from unittest.mock import AsyncMock

        from pymongo.errors import OperationFailure

        from ai_analytics_worker.source_repository import (
            get_ai_line_items_for_claim_with_retry,
        )

        monkeypatch.setattr(
            WorkerConfig, "max_retries", property(lambda self: 3)
        )
        monkeypatch.setattr(
            WorkerConfig, "source_query_timeout_ms", property(lambda self: 5000)
        )
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            AsyncMock(side_effect=OperationFailure("bad query")),
        )

        with pytest.raises(OperationFailure):
            await get_ai_line_items_for_claim_with_retry(object(), 100)

        assert worker_metrics.get("claim_refresh_retries") == 0


# ---------------------------------------------------------------------------
# change_stream_listener — event counters and health timestamps
# ---------------------------------------------------------------------------


class TestChangeStreamCounters:
    """Event volume counters and the resume-token checkpoint mirror."""

    @pytest.mark.asyncio
    async def test_watched_event_increments_events_received(self):
        queue = ClaimQueue(debounce_seconds=0.0)

        await _process_change_event(queue, make_change_event("insert", claim_id=100))

        assert worker_metrics.get("events_received") == 1
        assert worker_metrics.get("events_skipped_no_claim_id") == 0

    @pytest.mark.asyncio
    async def test_delete_event_counted_as_received(self):
        """Total stream volume includes deliberately-skipped operations.

        Otherwise an operator cannot tell a quiet stream from one delivering
        only deletes.
        """
        queue = ClaimQueue(debounce_seconds=0.0)

        await _process_change_event(queue, make_change_event("delete"))

        assert worker_metrics.get("events_received") == 1
        # A delete is skipped for lack of fullDocument, not for a missing
        # claim_id on a watched operation.
        assert worker_metrics.get("events_skipped_no_claim_id") == 0

    @pytest.mark.asyncio
    async def test_missing_claim_id_increments_skip_counter(self):
        queue = ClaimQueue(debounce_seconds=0.0)

        await _process_change_event(
            queue, make_change_event("insert", claim_id=None)
        )

        assert worker_metrics.get("events_received") == 1
        assert worker_metrics.get("events_skipped_no_claim_id") == 1
        assert queue.size == 0

    @pytest.mark.asyncio
    async def test_save_resume_token_mirrors_health_and_counts(
        self, mock_mongo_db
    ):
        """Saving a token must write Mongo *and* the in-memory health state.

        This is the divergence that made /ready report a null checkpoint
        while the Mongo document held the real value.
        """
        assert worker_health.last_checkpoint_at is None
        assert worker_health.last_successful_event_at is None

        await _save_resume_token(mock_mongo_db, {"_data": "abc"})

        assert worker_metrics.get("resume_tokens_saved") == 1
        assert worker_health.last_checkpoint_at is not None
        assert worker_health.last_successful_event_at is not None

        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["resume_token"] == {"_data": "abc"}


# ---------------------------------------------------------------------------
# queue consumer — checkpoint mirror
# ---------------------------------------------------------------------------


class TestQueueConsumerCheckpoint:
    """The consumer must mirror its Mongo checkpoint onto worker_health."""

    @pytest.mark.asyncio
    async def test_consumer_mirrors_checkpoint_to_health(
        self, mock_mongo_db, monkeypatch
    ):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            "ai_analytics_worker.queue.refresh_claim", AsyncMock()
        )
        queue = ClaimQueue(debounce_seconds=0.0)
        queue.enqueue(100)
        stop_event = asyncio.Event()

        assert worker_health.last_checkpoint_at is None

        task = asyncio.create_task(
            run_queue_consumer(mock_mongo_db, mock_mongo_db, queue, stop_event)
        )
        await asyncio.sleep(0.15)
        stop_event.set()
        queue.close()
        await asyncio.wait_for(task, timeout=2.0)

        # Both stores must agree that a checkpoint happened.
        assert worker_health.last_checkpoint_at is not None
        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["last_checkpoint_at"] is not None


# ---------------------------------------------------------------------------
# reconciliation — run and found counters
# ---------------------------------------------------------------------------


class FakeAsyncCursor:
    """Async cursor returning pre-seeded documents in batches."""

    def __init__(self, documents):
        self._docs = list(documents)

    def sort(self, field, direction):
        self._docs.sort(key=lambda d: str(d.get(field, "")))
        return self

    def batch_size(self, size):
        return self

    async def to_list(self, length=None):
        n = length if length is not None else len(self._docs)
        batch = self._docs[:n]
        self._docs = self._docs[n:]
        return batch


class FakeAsyncCollection:
    def __init__(self, documents):
        self._documents = list(documents)

    def find(self, query=None, projection=None):
        docs = self._documents
        if query and "updated_at" in query:
            gt = query["updated_at"]["$gt"]
            docs = [d for d in docs if d.get("updated_at") and d["updated_at"] > gt]
        return FakeAsyncCursor(docs)


class FakeAIDB:
    def __init__(self, collection):
        self._collection = collection

    def __getitem__(self, name):
        return self._collection


class TestReconciliationCounters:
    """Reconciliation must count real scans, and skip runs must not inflate."""

    @pytest.mark.asyncio
    async def test_scan_increments_runs_and_found(self, mock_mongo_db):
        now = datetime.now(UTC)
        await mock_mongo_db[worker_config.WORKER_STATE_COLLECTION].update_one(
            {"_id": worker_config.WORKER_NAME},
            {"$set": {"last_checkpoint_at": now - timedelta(hours=1)}},
            upsert=True,
        )
        docs = [
            {"_id": "d1", "claim_id": 100, "updated_at": now},
            {"_id": "d2", "claim_id": 200, "updated_at": now},
        ]
        ai_db = FakeAIDB(FakeAsyncCollection(docs))
        queue = ClaimQueue(debounce_seconds=0.0)

        await run_reconciliation_once(ai_db, mock_mongo_db, queue)

        assert worker_metrics.get("reconciliation_runs") == 1
        assert worker_metrics.get("reconciliation_claims_found") == 2

    @pytest.mark.asyncio
    async def test_skipped_run_does_not_increment_runs(self, mock_mongo_db):
        """No checkpoint means no scan happened, so the counter stays 0.

        This makes a zero counter a usable signal that the checkpoint is
        missing rather than that the loop is dead.
        """
        ai_db = FakeAIDB(FakeAsyncCollection([]))
        queue = ClaimQueue(debounce_seconds=0.0)

        await run_reconciliation_once(ai_db, mock_mongo_db, queue)

        assert worker_metrics.get("reconciliation_runs") == 0
        assert worker_metrics.get("reconciliation_claims_found") == 0


# ---------------------------------------------------------------------------
# backfill — run and processed counters
# ---------------------------------------------------------------------------


class TestBackfillCounters:
    """Backfill must count its run and each successfully projected claim."""

    @pytest.mark.asyncio
    async def test_backfill_increments_runs_and_processed(
        self, mock_mongo_db, monkeypatch
    ):
        from unittest.mock import AsyncMock

        ai_db = mock_mongo_db
        for cid in (100, 200, 300):
            await ai_db["ai_line_items"].insert_one(
                {"_id": ObjectId(), "claim_id": cid}
            )

        monkeypatch.setattr(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            AsyncMock(side_effect=lambda db, cid: make_full_doc(claim_id=cid)),
        )
        monkeypatch.setattr(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            AsyncMock(return_value=[]),
        )

        await run_backfill(ai_db, mock_mongo_db, batch_size=10)

        assert worker_metrics.get("backfill_runs") == 1
        assert worker_metrics.get("backfill_claims_processed") == 3
        # The shared refresh path counters move too.
        assert worker_metrics.get("claims_refreshed") == 3
        assert worker_metrics.get("projections_created") == 3

    @pytest.mark.asyncio
    async def test_failed_claim_not_counted_as_processed(
        self, mock_mongo_db, monkeypatch
    ):
        from unittest.mock import AsyncMock

        ai_db = mock_mongo_db
        for cid in (100, 200):
            await ai_db["ai_line_items"].insert_one(
                {"_id": ObjectId(), "claim_id": cid}
            )

        async def flaky(db, cid):
            if cid == 100:
                raise ConnectionFailure("dropped")
            return make_full_doc(claim_id=cid)

        monkeypatch.setattr(
            "ai_analytics_worker.claim_refresh.get_ai_line_items_for_claim_with_retry",
            flaky,
        )
        monkeypatch.setattr(
            "ai_analytics_worker.claim_refresh.get_agent_conversations_for_claim_with_retry",
            AsyncMock(return_value=[]),
        )

        await run_backfill(ai_db, mock_mongo_db, batch_size=10)

        assert worker_metrics.get("backfill_runs") == 1
        assert worker_metrics.get("backfill_claims_processed") == 1
        assert worker_metrics.get("dead_letters_created") == 1


# ---------------------------------------------------------------------------
# End-to-end: the /status endpoint reports non-zero after real work
# ---------------------------------------------------------------------------


class TestStatusEndpointReportsRealWork:
    """The regression guard for the original defect.

    The endpoint existed and returned 200, but every counter was zero and
    every timestamp null because nothing wrote them. This test drives real
    pipeline work and then asserts the HTTP payload reflects it.
    """

    @pytest.mark.asyncio
    async def test_status_payload_is_non_zero_after_refresh(
        self, mock_mongo_db, monkeypatch
    ):
        _patch_source(monkeypatch, line_items=make_full_doc(claim_id=100))
        await refresh_claim(mock_mongo_db, mock_mongo_db, 100, "insert")
        await _save_resume_token(mock_mongo_db, {"_data": "abc"})

        snapshot = worker_metrics.snapshot()
        assert snapshot["claims_refreshed"] == 1
        assert snapshot["projections_created"] == 1
        assert snapshot["resume_tokens_saved"] == 1
        assert any(v > 0 for v in snapshot.values())

        health = worker_health.snapshot()
        assert health["last_checkpoint_at"] is not None
        assert health["last_successful_event_at"] is not None

    def test_status_endpoint_serves_non_zero_counters(
        self, test_client, mock_mongo_db, monkeypatch
    ):
        """Through the real HTTP route, not just the singleton.

        ``asyncio.run`` is used rather than a manually created loop so the
        loop is closed on exit and does not leak into other tests. The
        TestClient call stays outside the coroutine because TestClient drives
        its own loop and would deadlock if invoked from inside a running one.
        """
        monkeypatch.setattr(
            WorkerConfig, "enabled", property(lambda self: True)
        )
        _patch_source(monkeypatch, line_items=make_full_doc(claim_id=100))

        asyncio.run(refresh_claim(mock_mongo_db, mock_mongo_db, 100, "insert"))

        response = test_client.get(
            "/api/ai-analytics/worker/status",
            headers={"Authorization": "Bearer valid-mock-token"},
        )
        assert response.status_code == 200
        metrics = response.json()["metrics"]
        assert metrics["claims_refreshed"] == 1
        assert metrics["projections_created"] == 1
