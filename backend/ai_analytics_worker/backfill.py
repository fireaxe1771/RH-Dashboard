"""AI Analytics Worker — historical backfill (Phase 4).

Populates the ``ai_invoice_analytics`` projection collection from existing
historical ``ai_line_items`` records. This is the one-time (or occasional)
full-scan mode that brings the projection up to date with all pre-existing
source data, complementing the incremental change-stream listener (Phase 5)
and reconciliation safety net (Phase 7).

Algorithm:
1. Open a cursor over ``ai_line_items`` projecting only ``claim_id``,
   sorted by ``_id`` for deterministic batching.
2. Process claims in batches of ``WORKER_BACKFILL_BATCH_SIZE``.
3. For each claim:
   a. Fetch the full ``ai_line_items`` doc (via ``source_repository`` with
      timeout/retry).
   b. Fetch conversations (via ``source_repository`` with timeout/retry).
   c. Build the projection (via ``projection_builder.build_projection``).
   d. Persist the projection (via ``projection_repository.upsert_projection``).
   e. On error: record a dead-letter and continue (one bad claim never stops
      the backfill).
4. Record a ``backfill`` run in ``ai_analytics_worker_runs`` with stats.
5. Respect the ``stop_event`` for graceful cancellation between batches.
6. Respect ``WORKER_MAX_CLAIMS_PER_CYCLE`` to prevent event-loop starvation
   during large backfills (Section 1.1.5).

Source: RecoveryHub_AI MongoDB (read-only via ``source_repository``).
Destination: ``ai_invoice_analytics``, ``ai_analytics_worker_runs``,
``ai_analytics_worker_dead_letters`` (dashboard-owned MongoDB).
Architectural constraints:
- Never writes to RecoveryHub_AI.
- Idempotent: re-running a backfill updates existing projections, not duplicates.
- Cancellable: checks ``stop_event`` between batches.
- One bad claim does not stop the backfill — dead-lettered and continued.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from bson import ObjectId

from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

from .claim_refresh import (
    OUTCOME_DEAD_LETTERED,
    OUTCOME_INSERTED,
    OUTCOME_NO_SOURCE,
    OUTCOME_UPDATED,
    refresh_claim,
)
from .config import worker_config
from .projection_repository import (
    record_worker_run,
    update_worker_run,
)

logger = logging.getLogger(__name__)


# Only the claim_id is needed for backfill enumeration. The full doc is
# fetched per-claim via source_repository (with timeout/retry).
_BACKFILL_ENUMERATION_PROJECTION = {"_id": 1, "claim_id": 1}


@dataclass
class BackfillResult:
    """Statistics returned by ``run_backfill`` after completion.

    Attributes:
        total_claim_ids: total distinct claim IDs discovered in the scan.
        claims_processed: claims that were successfully fetched + projected.
        claims_failed: claims that raised an error and were dead-lettered.
        claims_skipped: claims skipped because claim_id was None/invalid.
        projections_inserted: new projections created.
        projections_updated: existing projections updated.
        dead_lettered: dead-letter records written.
        started_at: when the backfill started.
        completed_at: when the backfill completed (None if cancelled).
        cancelled: True if the backfill was cancelled via stop_event.
        error: error message if the backfill failed fatally, else None.
    """

    total_claim_ids: int = 0
    claims_processed: int = 0
    claims_failed: int = 0
    claims_skipped: int = 0
    projections_inserted: int = 0
    projections_updated: int = 0
    dead_lettered: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: Optional[datetime] = None
    cancelled: bool = False
    error: Optional[str] = None


async def run_backfill(
    ai_db: Any,
    db: Any,
    stop_event: Optional[asyncio.Event] = None,
    batch_size: Optional[int] = None,
    max_claims_per_cycle: Optional[int] = None,
) -> BackfillResult:
    """Run a historical backfill of all ``ai_line_items`` claims.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (writes).
        stop_event: an ``asyncio.Event`` that the caller sets to request
            graceful cancellation. Checked between batches. If set, the
            backfill stops and ``BackfillResult.cancelled`` is True.
        batch_size: number of claim_ids to enumerate per batch query. Defaults
            to ``worker_config.backfill_batch_size``.
        max_claims_per_cycle: maximum claims to process before yielding. Defaults
            to ``worker_config.max_claims_per_cycle``. Prevents event-loop
            starvation during large backfills (Section 1.1.5).

    Returns:
        A ``BackfillResult`` with statistics about the run.

    Side effects:
        - Upserts projections into ``ai_invoice_analytics``.
        - Records a ``backfill`` run in ``ai_analytics_worker_runs`` (started,
          then updated with completion stats).
        - Records dead-letters in ``ai_analytics_worker_dead_letters`` for
          claims that failed processing.
    """
    if batch_size is None:
        batch_size = worker_config.backfill_batch_size
    if max_claims_per_cycle is None:
        max_claims_per_cycle = worker_config.max_claims_per_cycle

    result = BackfillResult()
    run_started_at = result.started_at

    # Record the start of the backfill run in the audit log.
    run_doc: Dict[str, Any] = {
        "run_type": "backfill",
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

    logger.info(
        "Backfill started (batch_size=%d, max_claims_per_cycle=%d, "
        "worker_version=%s).",
        batch_size,
        max_claims_per_cycle,
        worker_config.worker_version,
    )

    try:
        await _backfill_loop(
            ai_db=ai_db,
            db=db,
            result=result,
            stop_event=stop_event,
            batch_size=batch_size,
            max_claims_per_cycle=max_claims_per_cycle,
        )

        result.completed_at = datetime.now(UTC)
        final_status = "cancelled" if result.cancelled else "completed"
        await update_worker_run(
            db,
            run_id,
            {
                "completed_at": result.completed_at,
                "claims_processed": result.claims_processed,
                "claims_failed": result.claims_failed,
                "projections_created": result.projections_inserted,
                "projections_updated": result.projections_updated,
                "status": final_status,
                "error": result.error,
            },
        )
        logger.info(
            "Backfill %s (processed=%d, failed=%d, inserted=%d, updated=%d, "
            "dead_lettered=%d).",
            final_status,
            result.claims_processed,
            result.claims_failed,
            result.projections_inserted,
            result.projections_updated,
            result.dead_lettered,
        )
    except Exception as exc:
        result.error = str(exc)
        result.completed_at = datetime.now(UTC)
        await update_worker_run(
            db,
            run_id,
            {
                "completed_at": result.completed_at,
                "claims_processed": result.claims_processed,
                "claims_failed": result.claims_failed,
                "projections_created": result.projections_inserted,
                "projections_updated": result.projections_updated,
                "status": "failed",
                "error": result.error,
            },
        )
        logger.exception("Backfill failed fatally.")
        raise

    return result


async def _backfill_loop(
    ai_db: Any,
    db: Any,
    result: BackfillResult,
    stop_event: Optional[asyncio.Event],
    batch_size: int,
    max_claims_per_cycle: int,
) -> None:
    """Inner loop: enumerate claim_ids in batches and process each claim.

    Uses a cursor over ``ai_line_items`` with ``_id`` ordering for deterministic
    batching. The cursor is re-queried per batch with a ``_id > $last_id`` filter
    rather than held open, so a long backfill doesn't hold a cursor resource.
    """
    last_id: Optional[ObjectId] = None
    claims_since_yield = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            result.cancelled = True
            logger.info("Backfill cancelled by stop_event.")
            return

        # Fetch the next batch of claim_ids.
        query: Dict[str, Any] = {}
        if last_id is not None:
            query["_id"] = {"$gt": last_id}

        cursor = (
            ai_db[AI_LINE_ITEMS_COLLECTION]
            .find(query, _BACKFILL_ENUMERATION_PROJECTION)
            .sort("_id", 1)
            .limit(batch_size)
        )
        batch = await cursor.to_list(length=batch_size)

        if not batch:
            # No more documents — backfill complete.
            return

        # Process each claim in the batch.
        for doc in batch:
            if stop_event is not None and stop_event.is_set():
                result.cancelled = True
                logger.info("Backfill cancelled by stop_event.")
                return

            last_id = doc["_id"]
            raw_claim_id = doc.get("claim_id")
            claim_id: Optional[int] = None
            if raw_claim_id is not None:
                try:
                    claim_id = int(raw_claim_id)
                except (ValueError, TypeError):
                    claim_id = None

            if claim_id is None:
                result.claims_skipped += 1
                logger.warning(
                    "Backfill: skipping ai_line_items _id=%s with invalid "
                    "claim_id=%r.",
                    doc["_id"],
                    raw_claim_id,
                )
                continue

            result.total_claim_ids += 1

            await _process_single_claim(ai_db, db, claim_id, result)

            claims_since_yield += 1
            if claims_since_yield >= max_claims_per_cycle:
                # Yield control to the event loop so the FastAPI process
                # doesn't become unresponsive during a large backfill.
                await asyncio.sleep(0)
                claims_since_yield = 0


async def _process_single_claim(
    ai_db: Any,
    db: Any,
    claim_id: int,
    result: BackfillResult,
) -> None:
    """Refresh a single claim's projection via the shared ``refresh_claim``.

    Delegates to ``claim_refresh.refresh_claim`` (Phase 5 DRY extraction) and
    maps the returned ``ClaimRefreshResult`` to the backfill's running
    counters. ``asyncio.CancelledError`` propagates from ``refresh_claim``
    without being caught here — the backfill loop handles it.
    """
    refresh_result = await refresh_claim(
        ai_db=ai_db,
        db=db,
        claim_id=claim_id,
        source_event_type="backfill",
    )

    if refresh_result.outcome == OUTCOME_INSERTED:
        result.projections_inserted += 1
        result.claims_processed += 1
    elif refresh_result.outcome == OUTCOME_UPDATED:
        result.projections_updated += 1
        result.claims_processed += 1
    elif refresh_result.outcome in (OUTCOME_DEAD_LETTERED, OUTCOME_NO_SOURCE):
        result.dead_lettered += 1
        result.claims_failed += 1
