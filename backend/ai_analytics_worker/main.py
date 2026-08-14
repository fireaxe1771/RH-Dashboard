"""AI Analytics Worker — entry point, lifecycle, and asyncio task.

This module is the worker's main loop. In Phase 1 it is a no-op stub that
proves the lifespan integration, cancellation, and health-state transitions
work correctly. Subsequent phases replace the no-op body with the real
pipeline: backfill (Phase 4/6) → change-stream listener + reconciliation +
queue (Phase 7/9/10).

Source: none directly in Phase 1 (the stub does no I/O). Later phases read
RecoveryHub_AI MongoDB via ``source_repository``.
Destination: none directly in Phase 1. Later phases write projections via
``projection_repository``.
Architectural constraints:
- Runs as a single background asyncio task in the FastAPI event loop.
- Must respond to cancellation within ``CANCELLATION_TIMEOUT_SECONDS`` (5s).
- Must never block the event loop — all waits are ``await asyncio.sleep``.
- Updates ``worker_health`` on every lifecycle transition.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .config import worker_config
from .health import worker_health, STATUS_RUNNING, STATUS_STOPPED
from .metrics import worker_metrics

logger = logging.getLogger(__name__)

# Sleep interval for the no-op Phase 1 loop. Short enough that cancellation is
# observed quickly, long enough that the loop does not busy-spin. Real phases
# will replace this with the change-stream wait.
_NOOP_LOOP_INTERVAL_SECONDS = 0.5


async def run_worker(stop_event: asyncio.Event) -> None:
    """Run the AI Analytics Worker until ``stop_event`` is set or cancelled.

    Phase 1: no-op loop. Marks the worker as started/running, sleeps in short
    intervals so cancellation is observed promptly, then marks stopped on exit.

    Arguments:
        stop_event: an ``asyncio.Event`` that the caller sets to request a
            graceful shutdown. The worker checks this between cycles.

    Side effects:
        Updates ``worker_health`` (status, started/completed timestamps).
        Logs start and stop events.

    Meaningful exceptions:
        ``asyncio.CancelledError`` is re-raised after marking the worker
        stopped, so the caller's ``await task`` surfaces the cancellation.
        Any other exception is recorded on ``worker_health`` and re-raised.
    """
    worker_health.mark_started()
    worker_health.set_status(STATUS_RUNNING)
    logger.info(
        "AI Analytics Worker started (version=%s, projection_schema_version=%d).",
        worker_config.worker_version,
        worker_config.projection_schema_version,
    )

    try:
        while not stop_event.is_set():
            # Phase 1 no-op: yield control and re-check the stop event.
            # Real phases will await the change-stream listener / queue here.
            await asyncio.sleep(_NOOP_LOOP_INTERVAL_SECONDS)

        # Graceful shutdown via stop_event
        worker_health.mark_completed()
        logger.info("AI Analytics Worker received stop signal; shutting down.")
    except asyncio.CancelledError:
        # Cancellation (e.g. lifespan shutdown timeout) — mark stopped and
        # re-raise so the awaiting caller observes the cancellation.
        logger.info("AI Analytics Worker cancelled; shutting down.")
        raise
    except Exception as exc:
        # Unexpected error — record on health state and re-raise.
        worker_health.record_error(str(exc))
        logger.exception("AI Analytics Worker encountered an unexpected error.")
        raise
    finally:
        worker_health.set_status(STATUS_STOPPED)
        logger.info("AI Analytics Worker stopped.")


async def stop_worker_task(
    task: asyncio.Task,
    stop_event: asyncio.Event,
    timeout: float = worker_config.CANCELLATION_TIMEOUT_SECONDS,
) -> None:
    """Gracefully stop a running worker task within ``timeout`` seconds.

    Sets the stop event, awaits the task for up to ``timeout`` seconds, and
    cancels the task if it has not exited by then. This is the shutdown
    contract mandated by Section 1.1.4 of the Phase 0 plan.

    Arguments:
        task: the ``asyncio.Task`` returned by ``asyncio.create_task(run_worker(...))``.
        stop_event: the ``asyncio.Event`` passed to ``run_worker``.
        timeout: max seconds to wait for graceful shutdown before cancelling.

    Side effects:
        Sets ``stop_event``, awaits/cancels ``task``.

    Meaningful exceptions:
        Swallows ``asyncio.CancelledError`` from the task (expected on the
        cancellation path). Re-raises any other exception the task raised.
    """
    stop_event.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "AI Analytics Worker did not stop within %.1fs; cancelling.",
            timeout,
        )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        # Task was cancelled — expected.
        pass
