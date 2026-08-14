"""Unit tests for ai_analytics_worker.main (worker lifecycle and cancellation).

Feature under test: the AI Analytics Worker's Phase 1 no-op stub — its
lifecycle transitions (started → running → stopped), graceful shutdown via
stop_event, hard cancellation via task.cancel(), and the stop_worker_task
helper's timeout-then-cancel contract.

Failure prevented: a worker that cannot be stopped within the 5-second
cancellation deadline (Phase 0 plan Section 1.1.4) would hang the FastAPI
lifespan shutdown and prevent clean process termination.

Test level: unit.
"""

import asyncio
import logging

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


class TestRunWorkerLifecycle:
    """Tests that run_worker transitions health state correctly."""

    @pytest.mark.asyncio
    async def test_worker_starts_running_and_stops_on_stop_event(self):
        """Setting the stop event causes the worker to exit and mark stopped."""
        stop_event = asyncio.Event()
        task = asyncio.create_task(run_worker(stop_event))

        # Let the worker run at least one loop iteration
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
        task = asyncio.create_task(run_worker(stop_event))

        await asyncio.sleep(0.05)
        assert worker_health.status == STATUS_RUNNING

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The finally block must have set status to stopped
        assert worker_health.status == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_worker_records_unexpected_error_on_health(self, monkeypatch):
        """An unexpected exception is recorded on worker_health and re-raised."""
        stop_event = asyncio.Event()

        # Patch asyncio.sleep to raise a non-CancelledError to simulate an
        # unexpected failure mid-loop. monkeypatch auto-restores after the test
        # so the global asyncio module is not permanently mutated.
        async def exploding_sleep(seconds):
            raise RuntimeError("simulated mid-loop failure")

        monkeypatch.setattr(asyncio, "sleep", exploding_sleep)

        with pytest.raises(RuntimeError, match="simulated mid-loop failure"):
            await run_worker(stop_event)

        # record_error sets STATUS_ERROR; the finally block then sets
        # STATUS_STOPPED. So the final status is STOPPED but the error was
        # recorded on health state.
        assert worker_health.last_error is not None
        assert "simulated mid-loop failure" in worker_health.last_error
        assert worker_health.consecutive_error_count >= 1
        assert worker_health.status == STATUS_STOPPED


class TestStopWorkerTask:
    """Tests for the stop_worker_task shutdown helper."""

    @pytest.mark.asyncio
    async def test_stop_worker_task_graceful_within_timeout(self):
        """A cooperative worker stops within the timeout (no cancellation needed)."""
        stop_event = asyncio.Event()
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
