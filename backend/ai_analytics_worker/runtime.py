"""Runtime controller for the AI Analytics Worker task.

Centralises the asyncio task and stop-event management so that both the
FastAPI lifespan (``main.py``) and the runtime control endpoints
(``routes.py``) can start, stop, and inspect the worker without circular
imports.

Holds three pieces of module-level state:
- ``_worker_task`` — the ``asyncio.Task`` running ``run_worker``, or ``None``.
- ``_worker_stop_event`` — the ``asyncio.Event`` passed to ``run_worker``,
  or ``None``.
- ``_backfill_task`` — the ``asyncio.Task`` running ``run_backfill``, or
  ``None``. Tracked separately so the worker can be stopped without
  cancelling an in-flight backfill, and so a second backfill is rejected
  while one is already running.

Source: none (state management only).
Destination: none (delegates to ``run_worker`` / ``run_backfill``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Optional

from .main import run_worker, stop_worker_task
from .backfill import run_backfill
from .config import worker_config
from .health import worker_health, STATUS_RUNNING, STATUS_STOPPED
from .leader_election import (
    release_leadership,
    renew_leadership,
    try_acquire_leadership,
)

logger = logging.getLogger(__name__)

# Module-level handles — set by ``start_worker`` / ``start_backfill`` and
# cleared by ``stop_worker`` / the task completion callbacks.
# ``_worker_task`` is the leadership *campaign* task: it acquires the
# MongoDB leader lease and only then runs ``run_worker`` inside it, so a
# multi-process deployment (uvicorn --workers N) elects exactly one worker.
_worker_task: Optional[asyncio.Task] = None
_worker_stop_event: Optional[asyncio.Event] = None
_backfill_task: Optional[asyncio.Task] = None
_is_worker_leader: bool = False
_worker_holder_id: Optional[str] = None


def is_worker_running() -> bool:
    """Return True if the worker campaign task exists and has not finished.

    Note: "running" means the campaign loop is alive — the process may be
    leader (worker loops active) or a candidate waiting for the lease.
    """
    return _worker_task is not None and not _worker_task.done()


def is_worker_leader() -> bool:
    """Return True if this process currently holds the leader lease and is
    running the worker loops."""
    return _is_worker_leader


def worker_holder_id() -> Optional[str]:
    """The unique holder id this process campaigns under, or None."""
    return _worker_holder_id


async def _interruptible_sleep(stop_event: asyncio.Event, seconds: float) -> None:
    """Sleep for ``seconds`` or until ``stop_event`` is set, whichever
    comes first."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def _run_worker_while_leader(
    stop_event: asyncio.Event,
    ai_db,
    db,
    holder_id: str,
) -> None:
    """Run ``run_worker`` for as long as this process holds the lease.

    A renewal loop extends the lease every ``leader_renew_interval_seconds``.
    If renewal reports the lease lost (or errors), the worker loops are
    stopped via an inner stop event so this process never runs worker loops
    without holding the lease. ``run_worker`` finishing on its own (a
    sub-task fatal error) also ends the leadership term; the campaign loop
    then re-acquires and restarts it.
    """
    inner_stop = asyncio.Event()
    worker_task = asyncio.create_task(
        run_worker(inner_stop, ai_db=ai_db, db=db),
        name="ai_analytics_worker_inner",
    )

    async def _forward_global_stop() -> None:
        await stop_event.wait()
        inner_stop.set()

    forwarder = asyncio.create_task(_forward_global_stop())

    try:
        while not worker_task.done() and not inner_stop.is_set():
            await _interruptible_sleep(
                stop_event, worker_config.leader_renew_interval_seconds
            )
            if stop_event.is_set() or worker_task.done():
                break
            try:
                still_leader = await renew_leadership(
                    db, holder_id, worker_config.leader_lease_seconds
                )
            except Exception as exc:
                # A renewal error (network blip, failover) must not leave
                # worker loops running unleased — treat as lost.
                logger.warning(
                    "Worker leadership renewal errored (%r); treating the "
                    "lease as lost.",
                    exc,
                )
                still_leader = False
            if not still_leader:
                logger.warning(
                    "Worker leadership lost (holder=%s); stopping worker "
                    "loops.",
                    holder_id,
                )
                inner_stop.set()
                break

        # Wait for the worker loops to finish draining, however they were
        # asked to stop (lease loss, global stop, or sub-task failure).
        await asyncio.gather(worker_task, return_exceptions=True)

        if (
            worker_task.done()
            and not inner_stop.is_set()
            and not stop_event.is_set()
        ):
            exc = worker_task.exception()
            if exc is not None:
                logger.error(
                    "Worker task failed while holding leadership: %r — "
                    "will release the lease and re-campaign.",
                    exc,
                )
    finally:
        forwarder.cancel()
        inner_stop.set()
        if not worker_task.done():
            await asyncio.gather(worker_task, return_exceptions=True)


async def _run_leader_campaign(
    stop_event: asyncio.Event,
    ai_db,
    db,
) -> None:
    """Campaign loop: hold the worker lease, run worker loops while held.

    Exactly one process across the deployment owns the lease at a time.
    Non-leaders sleep ``leader_campaign_seconds`` and retry; if the leader
    dies, its lease expires and the next campaign tick takes over.
    """
    global _is_worker_leader, _worker_holder_id

    holder_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    _worker_holder_id = holder_id
    logger.info(
        "Worker leadership campaign started (holder=%s, lease=%ds, "
        "campaign=%ds).",
        holder_id,
        worker_config.leader_lease_seconds,
        worker_config.leader_campaign_seconds,
    )

    while not stop_event.is_set():
        try:
            acquired = await try_acquire_leadership(
                db, holder_id, worker_config.leader_lease_seconds
            )
        except Exception as exc:
            logger.warning(
                "Leader election attempt failed (%r); retrying in %ds.",
                exc,
                worker_config.leader_campaign_seconds,
            )
            await _interruptible_sleep(
                stop_event, worker_config.leader_campaign_seconds
            )
            continue

        if not acquired:
            await _interruptible_sleep(
                stop_event, worker_config.leader_campaign_seconds
            )
            continue

        _is_worker_leader = True
        logger.info(
            "Acquired AI Analytics Worker leadership (holder=%s).",
            holder_id,
        )
        try:
            await _run_worker_while_leader(stop_event, ai_db, db, holder_id)
        finally:
            _is_worker_leader = False
            try:
                await release_leadership(db, holder_id)
            except Exception as exc:
                logger.warning(
                    "Failed to release leadership lease: %r", exc
                )

    logger.info("Worker leadership campaign stopped (holder=%s).", holder_id)


def is_backfill_running() -> bool:
    """Return True if a backfill task exists and has not finished."""
    return _backfill_task is not None and not _backfill_task.done()


async def start_worker() -> str:
    """Start the AI Analytics Worker as a background asyncio task.

    The task is a leadership *campaign*: it acquires the MongoDB leader
    lease and only then runs the worker loops, so under ``uvicorn
    --workers N`` exactly one process is an active worker at a time.

    Returns a status string describing the outcome:
    - ``"started"`` — the worker was not running and has been started.
    - ``"already_running"`` — the worker is already running; no action taken.

    Raises:
        RuntimeError — if the dashboard-owned or AI Mongo database handles
        are not yet connected (``db_manager.connect()`` must have run).
    """
    global _worker_task, _worker_stop_event

    if is_worker_running():
        return "already_running"

    from database import db_manager
    if db_manager.db is None or db_manager.ai_db is None:
        raise RuntimeError(
            "Database connections not established. Cannot start worker."
        )

    _worker_stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(
        _run_leader_campaign(
            _worker_stop_event,
            ai_db=db_manager.ai_db,
            db=db_manager.db,
        ),
        name="ai_analytics_worker",
    )
    logger.info("AI Analytics Worker started via runtime control endpoint.")
    return "started"


async def stop_worker() -> str:
    """Stop the running AI Analytics Worker gracefully.

    Returns a status string:
    - ``"stopped"`` — the worker was running and has been stopped.
    - ``"not_running"`` — the worker was not running; no action taken.
    """
    global _worker_task, _worker_stop_event

    if not is_worker_running():
        # Clear stale references if the task finished on its own.
        _worker_task = None
        _worker_stop_event = None
        return "not_running"

    if _worker_task is None or _worker_stop_event is None:
        # Defensive: is_worker_running() returned True but handles are None.
        # Should not happen, but avoid AssertionError if it does.
        _worker_task = None
        _worker_stop_event = None
        return "not_running"

    await stop_worker_task(_worker_task, _worker_stop_event)
    _worker_task = None
    _worker_stop_event = None
    logger.info("AI Analytics Worker stopped via runtime control endpoint.")
    return "stopped"


async def start_backfill() -> str:
    """Trigger a historical backfill as a background asyncio task.

    The backfill reads all ``ai_line_items`` from the RecoveryHub_AI Mongo
    and upserts projections into ``ai_invoice_analytics``. It runs
    independently of the change-stream worker — the worker does not need
    to be running for a backfill to proceed.

    Returns a status string:
    - ``"started"`` — the backfill has been kicked off.
    - ``"already_running"`` — a backfill is already in progress.
    """
    global _backfill_task

    if is_backfill_running():
        return "already_running"

    from database import db_manager
    if db_manager.db is None or db_manager.ai_db is None:
        raise RuntimeError(
            "Database connections not established. Cannot start backfill."
        )

    async def _run_backfill_wrapper() -> None:
        try:
            result = await run_backfill(
                ai_db=db_manager.ai_db,
                db=db_manager.db,
                stop_event=None,
            )
            logger.info(
                "Backfill completed (processed=%d, failed=%d, "
                "inserted=%d, updated=%d).",
                result.claims_processed,
                result.claims_failed,
                result.projections_inserted,
                result.projections_updated,
            )
        except Exception as exc:
            logger.error("Backfill task failed: %s", exc)

    _backfill_task = asyncio.create_task(
        _run_backfill_wrapper(),
        name="ai_analytics_backfill",
    )
    logger.info("AI Analytics backfill started via runtime control endpoint.")
    return "started"


async def shutdown() -> None:
    """Stop the worker and cancel any in-flight backfill.

    Called from the FastAPI lifespan shutdown handler so that a clean
    container stop drains the worker and cancels the backfill.
    """
    await stop_worker()
    global _backfill_task
    if _backfill_task is not None and not _backfill_task.done():
        _backfill_task.cancel()
        try:
            await _backfill_task
        except asyncio.CancelledError:
            pass
    _backfill_task = None
