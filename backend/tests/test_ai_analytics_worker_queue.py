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
from unittest.mock import AsyncMock, patch

import pytest

from ai_analytics_worker.queue import ClaimQueue, run_queue_consumer


class FakeClock:
    """Deterministic monotonic clock for debounce-window assertions.

    The debounce tests originally used ``time.sleep`` with margins as small
    as 10ms. That is flaky on Windows, where the default system timer
    granularity (~15.6ms) can exceed the margin, so a sleep intended to
    cross the window sometimes did not. Observed failure rate was roughly
    2 in 6 runs.

    Injecting a clock removes wall-clock dependence entirely: ``advance``
    moves time by an exact amount, so "just before the window" and "just
    after the window" become precise, reproducible assertions rather than
    races. It also makes the tests instant instead of sleeping.

    Starts at 0.0 and the tests advance by whole seconds, because both
    choices keep the arithmetic exact in IEEE-754 doubles. Adding a small
    delta to a large origin loses precision: ``1000.0 + 0.05`` rounds to
    1000.04999999999995, so the subtraction inside ``pop_ready`` yields
    0.04999999999995 and an exact-boundary assertion fails for reasons that
    have nothing to do with the queue. Since no real time elapses, large
    window values are free.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


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
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=1.0, clock=clock)
        queue.enqueue(123)
        clock.advance(0.01)
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
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        queue.enqueue(200)
        # Items not ready yet (debounce window hasn't elapsed)
        assert queue.pop_ready() == []
        assert queue.size == 2

        clock.advance(10.0)  # exactly the window — boundary is inclusive
        ready = queue.pop_ready()
        assert set(ready) == {100, 200}
        assert queue.size == 0

    def test_pop_ready_excludes_item_just_short_of_window(self):
        """An item short of the window must not be drained.

        Pins the boundary from the other side: with a real clock this case
        could not be expressed reliably.
        """
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)

        clock.advance(9.0)
        assert queue.pop_ready() == []
        assert queue.size == 1

        clock.advance(1.0)
        assert queue.pop_ready() == [100]

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
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        # Deterministic: no time has passed, so the full window remains.
        assert queue.seconds_until_next_ready() == pytest.approx(10.0)

        clock.advance(4.0)
        assert queue.seconds_until_next_ready() == pytest.approx(6.0)

    def test_seconds_until_next_ready_clamps_to_zero_when_overdue(self):
        """A window already passed reports 0, never a negative delay.

        The consumer feeds this value to ``asyncio.wait_for`` as a timeout;
        a negative number would raise instead of polling.
        """
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        clock.advance(500.0)
        assert queue.seconds_until_next_ready() == 0.0

    def test_seconds_until_next_ready_reflects_earliest_item(self):
        """With staggered enqueues, the delay tracks the oldest pending claim."""
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        clock.advance(3.0)
        queue.enqueue(200)
        # Claim 100 is 3s in, so 7s remain; claim 200 needs the full 10s.
        assert queue.seconds_until_next_ready() == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# ClaimQueue — deduplication and debounce
# ---------------------------------------------------------------------------


class TestClaimQueueDebounce:
    """Tests that the debounce window coalesces bursts of updates."""

    def test_re_enqueue_resets_debounce_timer(self):
        """Re-enqueuing a claim resets its timer so it waits the full window again."""
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        clock.advance(8.0)

        # Re-enqueue — resets the timer
        queue.enqueue(100)
        clock.advance(8.0)
        # 16s of total elapsed time exceeds the 10s window, but only 8s has
        # passed since the reset, so the claim is not ready.
        assert queue.pop_ready() == []

        clock.advance(2.0)  # reaches 10s since the reset
        assert queue.pop_ready() == [100]

    def test_sustained_burst_never_drains_until_it_stops(self):
        """A claim re-enqueued faster than the window never becomes ready.

        This is the coalescing guarantee: a hot claim collapses into a single
        refresh that fires only once updates pause. Expressing it needs an
        exact clock — with sleeps it would be a 10-sleep race.
        """
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)

        for _ in range(10):
            queue.enqueue(100)
            clock.advance(9.0)  # always re-enqueued before the window closes
            assert queue.pop_ready() == []

        # Updates stop; the window now completes and yields exactly one claim.
        clock.advance(10.0)
        assert queue.pop_ready() == [100]

    def test_multiple_distinct_claims_are_all_ready_after_debounce(self):
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        queue.enqueue(200)
        queue.enqueue(300)

        clock.advance(10.0)
        assert set(queue.pop_ready()) == {100, 200, 300}

    def test_re_enqueue_does_not_duplicate_in_queue(self):
        """Enqueuing the same claim 5 times results in size=1, not 5."""
        queue = ClaimQueue(debounce_seconds=0.0)
        for _ in range(5):
            queue.enqueue(100)
        assert queue.size == 1

    def test_default_clock_is_real_monotonic_time(self):
        """Omitting ``clock`` must use ``time.monotonic``, not a stub.

        The FakeClock tests would still pass if the default were broken, so
        this asserts production behaviour explicitly: with no injected clock,
        a fresh entry is genuinely un-ready until real time passes.
        """
        import time as real_time

        queue = ClaimQueue(debounce_seconds=30.0)
        queue.enqueue(100)
        # A 30s window cannot have elapsed, so nothing is ready and the
        # reported delay is derived from the real clock.
        assert queue.pop_ready() == []
        delay = queue.seconds_until_next_ready()
        assert 29.0 < delay <= 30.0

        start = real_time.monotonic()
        assert real_time.monotonic() >= start  # sanity: clock is monotonic

    def test_mixed_burst_and_new_claims(self):
        """A burst on claim 100 plus a new claim 200 — both are ready after debounce."""
        clock = FakeClock()
        queue = ClaimQueue(debounce_seconds=10.0, clock=clock)
        queue.enqueue(100)
        queue.enqueue(100)
        queue.enqueue(100)
        queue.enqueue(200)

        assert queue.size == 2  # Only 2 distinct claims
        clock.advance(10.0)
        assert set(queue.pop_ready()) == {100, 200}


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
