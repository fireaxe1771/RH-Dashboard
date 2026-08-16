"""Unit tests for ai_analytics_worker.main (worker lifecycle and cancellation).

Feature under test: the AI Analytics Worker's lifecycle transitions (started →
running → stopped), graceful shutdown via stop_event, hard cancellation via
task.cancel(), and the stop_worker_task helper's timeout-then-cancel contract.

Phase 6/7: ``run_worker`` now spawns three concurrent sub-tasks (change-stream
listener, queue consumer, reconciliation loop). These tests mock all three so
they can test the lifecycle wrapper without real MongoDB connections.

Failure prevented: a worker that cannot be stopped within the 5-second
cancellation deadline (Phase 0 plan Section 1.1.4) would hang the FastAPI
lifespan shutdown and prevent clean process termination.

Test level: unit.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from ai_analytics_worker.config import worker_config
from ai_analytics_worker.health import (
    worker_health,
    STATUS_RUNNING,
    STATUS_STOPPED,
    STATUS_ERROR,
)
from ai_analytics_worker.main import run_worker, stop_worker_task


@pytest.fixture(autouse=True)
def reset_worker_health():
    """Reset the in-memory worker health state before each test."""
    worker_health.reset()
    yield


def _mock_subtask_that_waits_for_stop():
    """Return an AsyncMock that simulates a sub-task waiting for stop_event.

    The real sub-tasks (listener, consumer, reconciliation) all block until
    ``stop_event`` is set. This mock replicates that behavior: it awaits the
    stop_event (with a timeout) so the worker doesn't exit immediately.
    """

    async def _mock_subtask(*args, **kwargs):
        # Extract stop_event from kwargs or positional args
        stop_event = kwargs.get("stop_event")
        if stop_event is None:
            for arg in args:
                if isinstance(arg, asyncio.Event):
                    stop_event = arg
                    break
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    return AsyncMock(side_effect=_mock_subtask)


def _patch_all_subtasks():
    """Patch all three sub-task functions to wait for stop_event.

    Returns a list of patch context managers that should be entered with
    a ``with`` statement or patched individually.
    """
    return [
        patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=_mock_subtask_that_waits_for_stop(),
        ),
        patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ),
        patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ),
    ]


class TestRunWorkerLifecycle:
    """Tests that run_worker transitions health state correctly."""

    @pytest.mark.asyncio
    async def test_worker_starts_running_and_stops_on_stop_event(self):
        """Setting the stop event causes the worker to exit and mark stopped."""
        stop_event = asyncio.Event()
        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ):
            task = asyncio.create_task(run_worker(stop_event))

            # Let the worker start and enter the sub-tasks
            await asyncio.sleep(0.1)
            assert worker_health.status == STATUS_RUNNING
            assert worker_health.last_started_at is not None

            # Request graceful shutdown
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        assert worker_health.status == STATUS_STOPPED
        assert worker_health.last_completed_at is not None

    @pytest.mark.asyncio
    async def test_worker_marks_completed_on_graceful_shutdown(self):
        """Graceful shutdown via stop_event sets last_completed_at."""
        stop_event = asyncio.Event()
        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ):
            task = asyncio.create_task(run_worker(stop_event))

            await asyncio.sleep(0.05)
            completed_before = worker_health.last_completed_at
            assert completed_before is None

            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        assert worker_health.last_completed_at is not None

    @pytest.mark.asyncio
    async def test_worker_marks_stopped_on_cancellation(self):
        """Task.cancel() causes the worker to mark itself stopped."""
        stop_event = asyncio.Event()
        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ):
            task = asyncio.create_task(run_worker(stop_event))

            await asyncio.sleep(0.05)
            assert worker_health.status == STATUS_RUNNING

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # The finally block must have set status to stopped
        assert worker_health.status == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_worker_records_unexpected_error_on_health(self):
        """An unexpected exception from a sub-task is recorded on health and re-raised."""
        stop_event = asyncio.Event()

        # Mock the listener to raise an unexpected error (not CancelledError)
        exploding_listener = AsyncMock(
            side_effect=RuntimeError("simulated listener failure")
        )

        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=exploding_listener,
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ):
            with pytest.raises(RuntimeError, match="simulated listener failure"):
                await run_worker(stop_event)

        # record_error sets STATUS_ERROR; the finally block then sets
        # STATUS_STOPPED. So the final status is STOPPED but the error was
        # recorded on health state.
        assert worker_health.last_error is not None
        assert "simulated listener failure" in worker_health.last_error
        assert worker_health.consecutive_error_count >= 1
        assert worker_health.status == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_worker_spawns_three_subtasks(self):
        """run_worker spawns listener, consumer, and reconciliation tasks."""
        stop_event = asyncio.Event()
        call_counts = {"listener": 0, "consumer": 0, "recon": 0}

        async def counting_listener(*args, **kwargs):
            call_counts["listener"] += 1
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)

        async def counting_consumer(*args, **kwargs):
            call_counts["consumer"] += 1
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)

        async def counting_recon(*args, **kwargs):
            call_counts["recon"] += 1
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)

        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=AsyncMock(side_effect=counting_listener),
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=AsyncMock(side_effect=counting_consumer),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=AsyncMock(side_effect=counting_recon),
        ):
            task = asyncio.create_task(run_worker(stop_event))
            await asyncio.sleep(0.1)
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        assert call_counts["listener"] == 1
        assert call_counts["consumer"] == 1
        assert call_counts["recon"] == 1


class TestStopWorkerTask:
    """Tests for the stop_worker_task shutdown helper."""

    @pytest.mark.asyncio
    async def test_stop_worker_task_graceful_within_timeout(self):
        """A cooperative worker stops within the timeout (no cancellation needed)."""
        stop_event = asyncio.Event()
        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ):
            task = asyncio.create_task(run_worker(stop_event))

            await asyncio.sleep(0.05)
            await stop_worker_task(task, stop_event, timeout=2.0)

        assert task.done()
        assert worker_health.status == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_stop_worker_task_cancels_after_timeout(self):
        """A worker that ignores the stop event is cancelled after the timeout."""
        stop_event = asyncio.Event()

        # A worker that ignores the stop event and sleeps forever
        async def uncooperative_worker(ev):
            try:
                while True:
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(uncooperative_worker(stop_event))
        await asyncio.sleep(0.05)

        # Short timeout to trigger the cancellation path quickly
        await stop_worker_task(task, stop_event, timeout=0.3)

        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_worker_task_handles_already_done_task(self):
        """Stopping a task that already completed does not raise."""
        stop_event = asyncio.Event()

        async def quick_worker(ev):
            return "done"

        task = asyncio.create_task(quick_worker(stop_event))
        await asyncio.sleep(0.05)
        assert task.done()

        # Should not raise even though the task is already done
        await stop_worker_task(task, stop_event, timeout=1.0)


class TestWorkerConfigIntegration:
    """Tests that the worker reads worker_config correctly."""

    @pytest.mark.asyncio
    async def test_worker_logs_version_and_schema_version(self, caplog):
        """The startup log includes the worker version and schema version."""
        caplog.set_level(logging.INFO, logger="ai_analytics_worker.main")
        stop_event = asyncio.Event()
        with patch(
            "ai_analytics_worker.main.run_change_stream_listener",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_queue_consumer",
            new=_mock_subtask_that_waits_for_stop(),
        ), patch(
            "ai_analytics_worker.main.run_reconciliation_loop",
            new=_mock_subtask_that_waits_for_stop(),
        ):
            task = asyncio.create_task(run_worker(stop_event))

            await asyncio.sleep(0.05)
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        startup_logs = [
            r for r in caplog.records
            if "AI Analytics Worker started" in r.message
        ]
        assert len(startup_logs) == 1
        assert worker_config.worker_version in startup_logs[0].message
        assert str(worker_config.projection_schema_version) in startup_logs[0].message
