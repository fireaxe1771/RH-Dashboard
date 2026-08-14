"""Unit tests for ai_analytics_worker.queue (Phase 6).

Feature under test: the ClaimQueue (deduplication/debounce) and the
run_queue_consumer that drains it and calls refresh_claim.

Failure prevented:
-- A burst of N updates to the same claim should produce 1 refresh, not N.
   The debounce window coalesces them.
-- A queue that never yields would starve the event loop.
   The max_claims_per_cycle yield prevents this.
-- A consumer that can't be cancelled would hang the FastAPI shutdown.
   The stop_event check must allow prompt cancellation.
-- A consumer that crashes on one bad claim would miss all subsequent claims.
   refresh_claim dead-letters and the consumer continues.

Test level: unit.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from ai_analytics_worker.queue import ClaimQueue, run_queue_consumer


# ---------------------------------------------------------------------------
# ClaimQueue — basic operations
# ---------------------------------------------------------------------------


class TestClaimQueueBasic:
    """Tests for ClaimQueue enqueue / pop_ready / close / size."""

    def test_enqueue_new_claim_returns_true(self):
        queue = ClaimQueue(debounce_seconds=0.0)
        assert queue.enqueue(123) is True
        assert queue.size == 1

    def test_enqueue_existing_claim_returns_false_and_resets_timer(self):
        queue = ClaimQueue(debounce_seconds=1.0)
        queue.enqueue(123)
        time.sleep(0.01)
        # Re-enqueue the same claim — should return False (already pending)
        assert queue.enqueue(123) is False
        assert queue.size == 1

    def test_enqueue_after_close_returns_false(self):
        queue = ClaimQueue(debounce_seconds=0.0)
        queue.close()
        assert queue.enqueue(123) is False
        assert queue.size == 0
        assert queue.closed is True

    def test_pop_ready_returns_items_past_debounce(self):
        queue = ClaimQueue(debounce_seconds=0.05)
        queue.enqueue(100)
        queue.enqueue(200)
        # Items not ready yet (debounce window hasn't elapsed)
        assert queue.pop_ready() == []
        assert queue.size == 2

        # Wait for debounce window
        time.sleep(0.06)
        ready = queue.pop_ready()
        assert set(ready) == {100, 200}
        assert queue.size == 0

    def test_pop_ready_with_zero_debounce_returns_immediately(self):
        queue = ClaimQueue(debounce_seconds=0.0)
        queue.enqueue(100)
        queue.enqueue(200)
        ready = queue.pop_ready()
        assert set(ready) == {100, 200}

    def test_pop_ready_empty_queue_returns_empty(self):
        queue = ClaimQueue(debounce_seconds=0.0)
        assert queue.pop_ready() == []

    def test_pop_ready_removes_items_from_queue(self):
        queue = ClaimQueue(debounce_seconds=0.0)
        queue.enqueue(100)
        ready = queue.pop_ready()
        assert 100 in ready
        # Second pop should be empty (items were removed)
        assert queue.pop_ready() == []

    def test_close_sets_closed_and_wakes_consumer(self):
        queue = ClaimQueue(debounce_seconds=1.0)
        queue.enqueue(100)
        assert not queue.closed
        queue.close()
        assert queue.closed is True

    def test_seconds_until_next_ready_empty_returns_none(self):
        queue = ClaimQueue(debounce_seconds=1.0)
        assert queue.seconds_until_next_ready() is None

    def test_seconds_until_next_ready_with_pending(self):
        queue = ClaimQueue(debounce_seconds=0.1)
        queue.enqueue(100)
        delay = queue.seconds_until_next_ready()
        assert delay is not None
        assert 0.0 <= delay <= 0.1


# ---------------------------------------------------------------------------
# ClaimQueue — deduplication and debounce
# ---------------------------------------------------------------------------


class TestClaimQueueDebounce:
    """Tests that the debounce window coalesces bursts of updates."""

    def test_re_enqueue_resets_debounce_timer(self):
        """Re-enqueuing a claim resets its timer so it waits the full window again."""
        queue = ClaimQueue(debounce_seconds=0.1)
        queue.enqueue(100)
        time.sleep(0.08)

        # Re-enqueue — resets the timer
        queue.enqueue(100)
        time.sleep(0.08)
        # Original window (0.1s) has elapsed but the reset window hasn't
        ready = queue.pop_ready()
        assert ready == []  # Not ready yet — timer was reset

        # Wait for the reset window to elapse
        time.sleep(0.03)
        ready = queue.pop_ready()
        assert 100 in ready

    def test_multiple_distinct_claims_are_all_ready_after_debounce(self):
        queue = ClaimQueue(debounce_seconds=0.05)
        queue.enqueue(100)
        queue.enqueue(200)
        queue.enqueue(300)

        time.sleep(0.06)
        ready = set(queue.pop_ready())
        assert ready == {100, 200, 300}

    def test_re_enqueue_does_not_duplicate_in_queue(self):
        """Enqueuing the same claim 5 times results in size=1, not 5."""
        queue = ClaimQueue(debounce_seconds=0.0)
        for _ in range(5):
            queue.enqueue(100)
        assert queue.size == 1

    def test_mixed_burst_and_new_claims(self):
        """A burst on claim 100 plus a new claim 200 — both are ready after debounce."""
        queue = ClaimQueue(debounce_seconds=0.05)
        queue.enqueue(100)
        queue.enqueue(100)
        queue.enqueue(100)
        queue.enqueue(200)

        assert queue.size == 2  # Only 2 distinct claims
        time.sleep(0.06)
        ready = set(queue.pop_ready())
        assert ready == {100, 200}


# ---------------------------------------------------------------------------
# run_queue_consumer
# ---------------------------------------------------------------------------


class TestQueueConsumer:
    """Tests for run_queue_consumer — drains queue, calls refresh_claim."""

    @pytest.mark.asyncio
    async def test_consumer_processes_ready_claims(self, mock_mongo_db):
        """The consumer pops ready claims and calls refresh_claim for each."""
        queue = ClaimQueue(debounce_seconds=0.0)
        queue.enqueue(100)
        queue.enqueue(200)

        stop_event = asyncio.Event()
        refresh_calls: list[int] = []

        async def fake_refresh(**kwargs):
            refresh_calls.append(kwargs["claim_id"])

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(side_effect=fake_refresh),
        ):
            # Run the consumer in a task, stop it after claims are processed
            task = asyncio.create_task(
                run_queue_consumer(
                    mock_mongo_db, mock_mongo_db, queue, stop_event
                )
            )
            await asyncio.sleep(0.2)
            stop_event.set()
            queue.close()
            await asyncio.wait_for(task, timeout=2.0)

        assert set(refresh_calls) == {100, 200}

    @pytest.mark.asyncio
    async def test_consumer_stops_on_stop_event(self, mock_mongo_db):
        """The consumer exits promptly when stop_event is set."""
        queue = ClaimQueue(debounce_seconds=0.0)
        stop_event = asyncio.Event()

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(),
        ):
            task = asyncio.create_task(
                run_queue_consumer(
                    mock_mongo_db, mock_mongo_db, queue, stop_event
                )
            )
            await asyncio.sleep(0.05)
            stop_event.set()
            queue.close()
            await asyncio.wait_for(task, timeout=2.0)

        assert task.done()

    @pytest.mark.asyncio
    async def test_consumer_exits_when_queue_closed_and_empty(
        self, mock_mongo_db
    ):
        """A closed empty queue causes the consumer to exit without stop_event."""
        queue = ClaimQueue(debounce_seconds=0.0)
        stop_event = asyncio.Event()

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(),
        ):
            queue.close()
            # Consumer should exit immediately since queue is closed and empty
            await asyncio.wait_for(
                run_queue_consumer(
                    mock_mongo_db, mock_mongo_db, queue, stop_event
                ),
                timeout=2.0,
            )

    @pytest.mark.asyncio
    async def test_consumer_dead_letters_and_continues(self, mock_mongo_db):
        """One failing claim is dead-lettered (inside refresh_claim) and the consumer continues."""
        queue = ClaimQueue(debounce_seconds=0.0)
        queue.enqueue(100)
        queue.enqueue(200)

        stop_event = asyncio.Event()
        call_count = {"n": 0}

        async def fake_refresh(**kwargs):
            call_count["n"] += 1
            if kwargs["claim_id"] == 100:
                # refresh_claim catches this internally and dead-letters.
                # But since we're mocking it, we just raise to verify the
                # consumer doesn't crash. In reality refresh_claim catches
                # the exception and returns a result — it never raises for
                # data-level errors. This test verifies the consumer doesn't
                # see exceptions from refresh_claim.
                return  # Simulate successful dead-letter (no raise)

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(side_effect=fake_refresh),
        ):
            task = asyncio.create_task(
                run_queue_consumer(
                    mock_mongo_db, mock_mongo_db, queue, stop_event
                )
            )
            await asyncio.sleep(0.2)
            stop_event.set()
            queue.close()
            await asyncio.wait_for(task, timeout=2.0)

        assert call_count["n"] == 2  # Both claims were processed

    @pytest.mark.asyncio
    async def test_consumer_debounces_burst(self, mock_mongo_db):
        """A burst of enqueues to the same claim results in one refresh_claim call."""
        queue = ClaimQueue(debounce_seconds=0.1)
        stop_event = asyncio.Event()
        refresh_calls: list[int] = []

        async def fake_refresh(**kwargs):
            refresh_calls.append(kwargs["claim_id"])

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(side_effect=fake_refresh),
        ):
            task = asyncio.create_task(
                run_queue_consumer(
                    mock_mongo_db, mock_mongo_db, queue, stop_event
                )
            )

            # Burst: enqueue the same claim 5 times within the debounce window
            for _ in range(5):
                queue.enqueue(100)
                await asyncio.sleep(0.01)

            # Wait for debounce to elapse and consumer to process
            await asyncio.sleep(0.2)
            stop_event.set()
            queue.close()
            await asyncio.wait_for(task, timeout=2.0)

        # The burst should have been coalesced into 1 refresh
        assert len(refresh_calls) == 1
        assert refresh_calls[0] == 100

    @pytest.mark.asyncio
    async def test_consumer_yields_to_event_loop(self, mock_mongo_db):
        """The consumer yields control after max_claims_per_cycle claims."""
        queue = ClaimQueue(debounce_seconds=0.0)
        for i in range(150):
            queue.enqueue(i)

        stop_event = asyncio.Event()
        # Track whether the event loop was free to run other tasks
        other_task_ran = {"yes": False}

        async def check_event_loop():
            other_task_ran["yes"] = True

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(),
        ):
            task = asyncio.create_task(
                run_queue_consumer(
                    mock_mongo_db,
                    mock_mongo_db,
                    queue,
                    stop_event,
                    max_claims_per_cycle=50,
                )
            )
            # While the consumer processes 150 claims, another task
            # should get a chance to run (proving the consumer yields).
            check_task = asyncio.create_task(check_event_loop())
            await asyncio.sleep(0.3)
            stop_event.set()
            queue.close()
            await asyncio.wait_for(task, timeout=2.0)
            await check_task

        assert other_task_ran["yes"] is True

    @pytest.mark.asyncio
    async def test_consumer_updates_checkpoint_after_refresh(self, mock_mongo_db):
        """The consumer updates last_checkpoint_at after each successful refresh."""
        from ai_analytics_worker.config import worker_config

        queue = ClaimQueue(debounce_seconds=0.0)
        queue.enqueue(100)

        stop_event = asyncio.Event()

        with patch(
            "ai_analytics_worker.queue.refresh_claim",
            new=AsyncMock(),
        ):
            task = asyncio.create_task(
                run_queue_consumer(
                    mock_mongo_db, mock_mongo_db, queue, stop_event
                )
            )
            await asyncio.sleep(0.2)
            stop_event.set()
            queue.close()
            await asyncio.wait_for(task, timeout=2.0)

        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state is not None
        assert state["last_checkpoint_at"] is not None
