"""AI Analytics Worker — entry point, lifecycle, and asyncio task.

This module is the worker's main entry point. It manages the worker lifecycle
(health state transitions, logging, cancellation) and orchestrates three
concurrent sub-tasks:

1. **Change-stream listener** (Phase 5/6) — watches RecoveryHub_AI MongoDB
   for changes, extracts claim_ids, and enqueues them into the ClaimQueue.
2. **Queue consumer** (Phase 6) — drains the ClaimQueue with a debounce
   window and calls ``refresh_claim`` for each claim.
3. **Reconciliation loop** (Phase 7) — periodically scans
   ``ai_line_items`` for claims updated since the last checkpoint and
   enqueues them into the same queue (safety net for missed events).
4. **Sync integrity loop** (Phase 11) — periodically verifies the
   projection cache matches the source MongoDB (count comparison +
   sample verification) and auto-re-enqueues divergent claims for
   refresh.

All four share the same ``stop_event`` and ``ClaimQueue``. On graceful
shutdown (``stop_event``) or cancellation (``asyncio.CancelledError``),
``run_worker`` shuts down all sub-tasks and marks the worker as stopped.

Source: RecoveryHub_AI MongoDB (read-only, via ``db_manager.ai_db`` or
the ``ai_db`` parameter).
Destination: dashboard-owned MongoDB (via ``db_manager.db`` or the ``db``
parameter) — projections, worker state, dead-letters, run audit log.
Architectural constraints:
- Runs as a single background asyncio task in the FastAPI event loop.
- Must respond to cancellation within ``CANCELLATION_TIMEOUT_SECONDS`` (5s).
- Must never block the event loop — all waits are ``await asyncio.sleep``.
- Updates ``worker_health`` on every lifecycle transition.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from .change_stream_listener import run_change_stream_listener
from .config import worker_config
from .health import worker_health, STATUS_RUNNING, STATUS_STOPPED
from .queue import ClaimQueue, run_queue_consumer
from .reconciliation import run_reconciliation_loop
from .sync_integrity import run_sync_integrity_loop

logger = logging.getLogger(__name__)


async def run_worker(
    stop_event: asyncio.Event,
    ai_db: Any = None,
    db: Any = None,
) -> None:
    """Run the AI Analytics Worker until ``stop_event`` is set or cancelled.

    Marks the worker as started/running, spawns three concurrent sub-tasks
    (change-stream listener, queue consumer, reconciliation loop), and
    supervises them until ``stop_event`` is set or a sub-task fails fatally.

    Arguments:
        stop_event: an ``asyncio.Event`` that the caller sets to request a
            graceful shutdown. The worker checks this between events.
        ai_db: the RecoveryHub_AI Motor database handle (read-only). If
            ``None``, pulled from ``database.db_manager.ai_db`` at call time.
        db: the dashboard-owned Motor database handle (writes). If ``None``,
            pulled from ``database.db_manager.db`` at call time.

    Side effects:
        Updates ``worker_health`` (status, started/completed timestamps).
        Logs start and stop events.
        Delegates projection writes to the queue consumer (via refresh_claim).

    Meaningful exceptions:
        ``asyncio.CancelledError`` is re-raised after marking the worker
        stopped, so the caller's ``await task`` surfaces the cancellation.
        Any other exception is recorded on ``worker_health`` and re-raised.
    """
    # Resolve database handles if not provided explicitly.
    if ai_db is None or db is None:
        from database import db_manager
        if ai_db is None:
            ai_db = db_manager.ai_db
        if db is None:
            db = db_manager.db

    worker_health.mark_started()
    worker_health.set_status(STATUS_RUNNING)
    logger.info(
        "AI Analytics Worker started (version=%s, projection_schema_version=%d).",
        worker_config.worker_version,
        worker_config.projection_schema_version,
    )

    queue = ClaimQueue(debounce_seconds=worker_config.debounce_seconds)

    tasks: List[asyncio.Task] = [
        asyncio.create_task(
            run_change_stream_listener(
                ai_db=ai_db,
                db=db,
                stop_event=stop_event,
                queue=queue,
            ),
            name="change_stream_listener",
        ),
        asyncio.create_task(
            run_queue_consumer(
                ai_db=ai_db,
                db=db,
                queue=queue,
                stop_event=stop_event,
            ),
            name="queue_consumer",
        ),
        asyncio.create_task(
            run_reconciliation_loop(
                ai_db=ai_db,
                db=db,
                stop_event=stop_event,
                queue=queue,
            ),
            name="reconciliation_loop",
        ),
        asyncio.create_task(
            run_sync_integrity_loop(
                ai_db=ai_db,
                db=db,
                stop_event=stop_event,
                queue=queue,
            ),
            name="sync_integrity_loop",
        ),
    ]

    first_error: Optional[BaseException] = None

    try:
        # Wait for stop_event to be set (external shutdown) or any sub-task
        # to complete/fail (FIRST_COMPLETED). A stop_waiter task lets us
        # combine ``stop_event.wait()`` with the sub-task futures in a
        # single ``asyncio.wait`` call.
        stop_waiter = asyncio.create_task(stop_event.wait(), name="stop_waiter")
        done, _ = await asyncio.wait(
            [*tasks, stop_waiter],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Collect any sub-task errors.
        for t in done:
            if t in tasks:
                exc = t.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    first_error = exc
                    logger.error(
                        "Worker sub-task %s failed: %r",
                        t.get_name(),
                        exc,
                    )
                    stop_event.set()

        # Clean shutdown: close the queue (wakes the consumer) and wait
        # for all sub-tasks to finish within the cancellation timeout.
        queue.close()
        if not stop_waiter.done():
            stop_waiter.cancel()
            try:
                await stop_waiter
            except asyncio.CancelledError:
                pass

        _, pending = await asyncio.wait(
            tasks,
            timeout=worker_config.CANCELLATION_TIMEOUT_SECONDS,
        )
        for t in pending:
            t.cancel()
        # Gather with return_exceptions so cancellation errors don't
        # propagate (they're expected during shutdown).
        await asyncio.gather(*tasks, return_exceptions=True)

        if first_error is not None:
            worker_health.record_error(str(first_error))
            logger.exception(
                "AI Analytics Worker encountered an unexpected error.",
            )
            raise first_error

        worker_health.mark_completed()
        logger.info("AI Analytics Worker received stop signal; shutting down.")

    except asyncio.CancelledError:
        # Cancellation (e.g. lifespan shutdown timeout) — cancel all
        # sub-tasks and re-raise so the awaiting caller observes it.
        logger.info("AI Analytics Worker cancelled; shutting down.")
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
