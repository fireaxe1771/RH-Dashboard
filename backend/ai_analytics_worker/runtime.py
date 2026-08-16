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
from typing import Optional

from .main import run_worker, stop_worker_task
from .backfill import run_backfill
from .config import worker_config
from .health import worker_health, STATUS_RUNNING, STATUS_STOPPED

logger = logging.getLogger(__name__)

# Module-level handles — set by ``start_worker`` / ``start_backfill`` and
# cleared by ``stop_worker`` / the task completion callbacks.
_worker_task: Optional[asyncio.Task] = None
_worker_stop_event: Optional[asyncio.Event] = None
_backfill_task: Optional[asyncio.Task] = None


def is_worker_running() -> bool:
    """Return True if the worker task exists and has not finished."""
    return _worker_task is not None and not _worker_task.done()


def is_backfill_running() -> bool:
    """Return True if a backfill task exists and has not finished."""
    return _backfill_task is not None and not _backfill_task.done()


async def start_worker() -> str:
    """Start the AI Analytics Worker as a background asyncio task.

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
        run_worker(
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

    assert _worker_task is not None
    assert _worker_stop_event is not None

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
