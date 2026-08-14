"""AI Analytics Worker — entry point, lifecycle, and asyncio task.

This module is the worker's main entry point. It manages the worker lifecycle
(health state transitions, logging, cancellation) and delegates the real work
to the change-stream listener (Phase 5).

The worker runs as a single background asyncio task in the FastAPI event loop.
On startup, ``run_worker`` marks the worker as running, then enters the
change-stream listener loop. On graceful shutdown (``stop_event``) or
cancellation (``asyncio.CancelledError``), it marks the worker as stopped and
returns/c re-raises.

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
from typing import Any

from .change_stream_listener import run_change_stream_listener
from .config import worker_config
from .health import worker_health, STATUS_RUNNING, STATUS_STOPPED

logger = logging.getLogger(__name__)


async def run_worker(
    stop_event: asyncio.Event,
    ai_db: Any = None,
    db: Any = None,
) -> None:
    """Run the AI Analytics Worker until ``stop_event`` is set or cancelled.

    Marks the worker as started/running, delegates to the change-stream
    listener (Phase 5), then marks stopped on exit.

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
        Delegates all projection writes to the change-stream listener.

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

    try:
        await run_change_stream_listener(
            ai_db=ai_db,
            db=db,
            stop_event=stop_event,
        )

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
