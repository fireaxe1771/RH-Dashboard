"""AI Analytics Worker — safety-net reconciliation (Phase 7).

Periodically scans ``ai_line_items`` for documents with ``updated_at`` newer
than the worker's last checkpoint, extracts claim_ids, and enqueues them
into the same ``ClaimQueue`` as the change-stream listener. This catches:

- Events missed because the oplog window was exceeded during a long outage.
- Events lost when the worker crashed between enqueue and refresh (the
  in-memory queue is not durable).
- Delete events (the listener skips deletes; reconciliation re-examines the
  claim and the projection is dead-lettered or marked stale by
  ``refresh_claim`` if the source is gone).

Design (per Phase 0 plan Section 8.6):
- Cadence: every ``WORKER_RECONCILIATION_INTERVAL_MINUTES`` minutes (default 30).
- Method: ``ai_line_items.find({"updated_at": {"$gt": watermark}})`` where
  ``watermark = last_checkpoint_at - safety_margin``. The safety margin
  covers the gap between the change-stream listener enqueuing an event and
  the queue consumer processing it, so claims enqueued but not yet refreshed
  are still re-examined.
- This is NOT a full historical scan — it only looks at records changed
  since the last known checkpoint (minus the safety margin).
- If ``last_checkpoint_at`` is None (first run, no backfill yet),
  reconciliation is skipped — the backfill (Phase 4) handles initial
  population.
- Claim_ids are deduplicated and enqueued into the queue; the consumer
  handles the actual refresh. ``refresh_claim`` is idempotent (upsert),
  so processing a claim that was already refreshed is harmless.

Source: RecoveryHub_AI MongoDB (read-only — scans ``ai_line_items``).
Destination: ``ai_analytics_worker_runs`` (audit log), and enqueues into
the in-memory ``ClaimQueue`` (which the consumer drains via
``claim_refresh``).
Architectural constraints:
-- Never writes to RecoveryHub_AI.
-- Cancellable via ``stop_event`` — checks between batches.
-- Records a ``reconciliation`` run in ``ai_analytics_worker_runs`` for
   traceability.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

from .config import worker_config
from .metrics import worker_metrics
from .projection_repository import get_worker_state, record_worker_run, update_worker_run
from .queue import ClaimQueue

logger = logging.getLogger(__name__)

# Safety margin subtracted from ``last_checkpoint_at`` when computing the
# reconciliation watermark. This covers the gap between the change-stream
# listener enqueuing an event (which sets ``last_checkpoint_at``) and the
# queue consumer actually processing it. Without this margin, a claim
# enqueued but not yet processed when the worker crashes would be missed
# by reconciliation (its ``updated_at`` would be before the checkpoint).
_RECONCILIATION_SAFETY_MARGIN_MINUTES = 5.0

# Only claim_id is needed from the scan — the full doc is fetched per-claim
# by the consumer via ``refresh_claim`` / ``source_repository``.
_RECONCILIATION_PROJECTION = {"_id": 1, "claim_id": 1}


@dataclass
class ReconciliationResult:
    """Statistics returned by ``run_reconciliation_once``.

    Attributes:
        claims_found: total documents found in the scan (may include
            duplicates if multiple ai_line_items share a claim_id).
        claims_enqueued: distinct claim_ids enqueued into the queue.
        claims_skipped: documents skipped due to missing/invalid claim_id.
        watermark: the ``updated_at`` threshold used for the scan.
        started_at: when the reconciliation scan started.
        completed_at: when the scan completed (None if cancelled).
        cancelled: True if the scan was cancelled via stop_event.
        error: error message if the scan failed fatally, else None.
    """

    claims_found: int = 0
    claims_enqueued: int = 0
    claims_skipped: int = 0
    watermark: Optional[datetime] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: Optional[datetime] = None
    cancelled: bool = False
    error: Optional[str] = None


async def run_reconciliation_once(
    ai_db: Any,
    db: Any,
    queue: ClaimQueue,
    batch_size: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> ReconciliationResult:
    """Run a single reconciliation scan and enqueue found claim_ids.

    Loads ``last_checkpoint_at`` from the worker state, computes the
    watermark (checkpoint minus safety margin), scans ``ai_line_items``
    for documents with ``updated_at > watermark``, and enqueues each
    distinct claim_id into the queue. Records a ``reconciliation`` run
    in ``ai_analytics_worker_runs``.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (writes run audit).
        queue: the ``ClaimQueue`` to enqueue found claim_ids into.
        batch_size: cursor batch size. Defaults to
            ``worker_config.backfill_batch_size``.
        stop_event: optional ``asyncio.Event`` for graceful cancellation.
            Checked between batches.

    Returns:
        A ``ReconciliationResult`` with statistics about the scan.

    Side effects:
        - Enqueues claim_ids into ``queue`` (consumer processes them).
        - Records a ``reconciliation`` run in ``ai_analytics_worker_runs``.
    """
    if batch_size is None:
        batch_size = worker_config.backfill_batch_size

    result = ReconciliationResult()
    run_started_at = result.started_at

    # Record the start of the reconciliation run.
    run_doc: Dict[str, Any] = {
        "run_type": "reconciliation",
        "started_at": run_started_at,
        "completed_at": None,
        "claims_processed": 0,
        "claims_failed": 0,
        "projections_created": 0,
        "projections_updated": 0,
        "status": "running",
        "error": None,
        "worker_version": worker_config.worker_version,
    }
    run_id = await record_worker_run(db, run_doc)

    try:
        # Load the checkpoint from worker state.
        state = await get_worker_state(db, worker_config.WORKER_NAME)
        if state is None or state.get("last_checkpoint_at") is None:
            # No checkpoint yet — skip reconciliation. The backfill
            # (Phase 4) handles initial population.
            result.completed_at = datetime.now(UTC)
            await update_worker_run(
                db,
                run_id,
                {
                    "completed_at": result.completed_at,
                    "status": "skipped",
                    "error": "No checkpoint — first run or backfill not completed.",
                },
            )
            logger.info(
                "Reconciliation: skipped (no last_checkpoint_at in worker state)."
            )
            return result

        last_checkpoint = state["last_checkpoint_at"]
        # Ensure the checkpoint is timezone-aware. Motor may return a
        # naive UTC datetime depending on the driver version.
        if last_checkpoint.tzinfo is None:
            last_checkpoint = last_checkpoint.replace(tzinfo=UTC)

        watermark = last_checkpoint - timedelta(
            minutes=_RECONCILIATION_SAFETY_MARGIN_MINUTES
        )
        result.watermark = watermark

        # Counted only once the scan actually proceeds — a run skipped for a
        # missing checkpoint is not a reconciliation. This makes a counter
        # stuck at 0 a meaningful signal that the checkpoint is absent.
        # Skipped runs are still recorded in ai_analytics_worker_runs.
        worker_metrics.increment("reconciliation_runs")

        logger.info(
            "Reconciliation: scanning ai_line_items for updated_at > %s "
            "(checkpoint=%s, safety_margin=%.0fm).",
            watermark.isoformat(),
            last_checkpoint.isoformat(),
            _RECONCILIATION_SAFETY_MARGIN_MINUTES,
        )

        seen_claim_ids: set[int] = set()

        # Scan in batches using a cursor with updated_at > watermark,
        # sorted by _id for deterministic batching.
        query = {"updated_at": {"$gt": watermark}}
        cursor = (
            ai_db[AI_LINE_ITEMS_COLLECTION]
            .find(query, _RECONCILIATION_PROJECTION)
            .sort("_id", 1)
            .batch_size(batch_size)
        )

        batch = await cursor.to_list(length=batch_size)
        while batch:
            if stop_event is not None and stop_event.is_set():
                result.cancelled = True
                logger.info("Reconciliation: cancelled by stop_event.")
                break

            for doc in batch:
                result.claims_found += 1
                raw_claim_id = doc.get("claim_id")
                claim_id: Optional[int] = None
                if raw_claim_id is not None:
                    try:
                        claim_id = int(raw_claim_id)
                    except (ValueError, TypeError):
                        claim_id = None

                if claim_id is None:
                    result.claims_skipped += 1
                    continue

                if claim_id not in seen_claim_ids:
                    seen_claim_ids.add(claim_id)
                    queue.enqueue(claim_id)
                    result.claims_enqueued += 1

            if stop_event is not None and stop_event.is_set():
                result.cancelled = True
                break

            batch = await cursor.to_list(length=batch_size)

        result.completed_at = datetime.now(UTC)
        # Matches the counter name: total source documents matching the
        # watermark, including multiple documents for the same claim. The
        # deduplicated count is ``claims_processed`` on the run audit record.
        if result.claims_found:
            worker_metrics.increment(
                "reconciliation_claims_found", result.claims_found
            )
        final_status = "cancelled" if result.cancelled else "completed"
        await update_worker_run(
            db,
            run_id,
            {
                "completed_at": result.completed_at,
                "claims_processed": result.claims_enqueued,
                "claims_failed": 0,
                "status": final_status,
                "error": result.error,
            },
        )
        logger.info(
            "Reconciliation %s (found=%d, enqueued=%d, skipped=%d).",
            final_status,
            result.claims_found,
            result.claims_enqueued,
            result.claims_skipped,
        )

    except Exception as exc:
        result.error = str(exc)
        result.completed_at = datetime.now(UTC)
        await update_worker_run(
            db,
            run_id,
            {
                "completed_at": result.completed_at,
                "claims_processed": result.claims_enqueued,
                "status": "failed",
                "error": result.error,
            },
        )
        logger.exception("Reconciliation failed fatally.")
        raise

    return result


async def run_reconciliation_loop(
    ai_db: Any,
    db: Any,
    stop_event: asyncio.Event,
    queue: ClaimQueue,
    interval_minutes: Optional[int] = None,
    batch_size: Optional[int] = None,
) -> None:
    """Run periodic reconciliation scans until ``stop_event`` is set.

    Sleeps for ``interval_minutes`` between scans. The first scan runs
    immediately on startup (to catch any gaps from a prior crash), then
    subsequent scans run on the configured interval.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (writes run audit).
        stop_event: an ``asyncio.Event`` that the caller sets to request
            graceful shutdown. Checked between scans and during sleep.
        queue: the ``ClaimQueue`` to enqueue found claim_ids into.
        interval_minutes: minutes between reconciliation scans. Defaults
            to ``worker_config.reconciliation_interval_minutes``.
        batch_size: cursor batch size. Defaults to
            ``worker_config.backfill_batch_size``.

    Side effects:
        - Periodically enqueues claim_ids into ``queue``.
        - Records ``reconciliation`` runs in ``ai_analytics_worker_runs``.

    Raises:
        ``asyncio.CancelledError``: if the caller cancels the task.
        Any exception from a fatal reconciliation scan error (the loop
        does not retry — the caller handles restart).
    """
    if interval_minutes is None:
        interval_minutes = worker_config.reconciliation_interval_minutes
    if batch_size is None:
        batch_size = worker_config.backfill_batch_size

    interval_seconds = interval_minutes * 60

    logger.info(
        "Reconciliation loop started (interval=%dm, batch_size=%d).",
        interval_minutes,
        batch_size,
    )

    while not stop_event.is_set():
        # Run a single reconciliation scan. Errors from the scan are
        # logged and the loop continues — a transient failure in one
        # scan should not stop future scans. Fatal errors (e.g. auth
        # failure) will repeat and eventually need operator attention,
        # but that's preferable to silently stopping the safety net.
        try:
            await run_reconciliation_once(
                ai_db=ai_db,
                db=db,
                queue=queue,
                batch_size=batch_size,
                stop_event=stop_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Reconciliation scan failed (error_type=%s); will retry "
                "on next interval.",
                type(exc).__name__,
            )

        if stop_event.is_set():
            break

        # Sleep until the next scan, checking stop_event periodically
        # so the loop exits promptly on shutdown.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass  # Interval elapsed — run next scan
        except asyncio.CancelledError:
            raise

    logger.info("Reconciliation loop: stopped via stop_event.")
