"""AI Analytics Worker — single-claim refresh algorithm (Phase 5).

Shared logic for refreshing a single claim's projection: fetch source data
(via ``source_repository`` with timeout/retry from Phase 2), build the
projection (via ``projection_builder.build_projection`` from Phase 3), and
persist it (via ``projection_repository.upsert_projection`` from Phase 4).

This module is the DRY boundary between the two callers that need to refresh
a single claim:

- **Historical backfill** (Phase 4, ``backfill.py``) — calls ``refresh_claim``
  for each claim discovered in the full ``ai_line_items`` scan.
- **Change-stream listener** (Phase 5, ``change_stream_listener.py``) — calls
  ``refresh_claim`` for each change event that affects a claim.

On any error, the claim is dead-lettered (via ``projection_repository.record_dead_letter``)
and the error is logged. ``asyncio.CancelledError`` is re-raised without
dead-lettering — external cancellation must propagate cleanly.

Source: RecoveryHub_AI MongoDB (read-only via ``source_repository``).
Destination: ``ai_invoice_analytics``, ``ai_analytics_worker_dead_letters``
(via ``projection_repository``).
Architectural constraints:
- Never writes to RecoveryHub_AI.
- Idempotent: refreshing the same claim twice produces the same projection
  (upsert, not insert).
- Never raises for data-level errors — dead-letters and returns a result.
  Only ``asyncio.CancelledError`` propagates.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from .metrics import worker_metrics
from .projection_builder import build_projection
from .projection_repository import record_dead_letter, upsert_projection
from .source_repository import (
    get_ai_line_items_for_claim_with_retry,
    get_agent_conversations_for_claim_with_retry,
)

logger = logging.getLogger(__name__)


# Outcome strings returned by ``refresh_claim``. Stable — callers and tests
# switch on these.
OUTCOME_INSERTED = "inserted"
OUTCOME_UPDATED = "updated"
OUTCOME_DEAD_LETTERED = "dead_lettered"
OUTCOME_NO_SOURCE = "no_source"


@dataclass
class ClaimRefreshResult:
    """Result of refreshing a single claim's projection.

    Attributes:
        outcome: one of ``OUTCOME_INSERTED`` / ``OUTCOME_UPDATED`` /
            ``OUTCOME_DEAD_LETTERED`` / ``OUTCOME_NO_SOURCE``.
        claim_id: the claim that was refreshed (may differ from the input
            if the source document's claim_id was used as fallback).
        error_type: exception class name if an error occurred, else None.
        error_message: error message string if an error occurred, else None.
    """

    outcome: str
    claim_id: Optional[int]
    error_type: Optional[str] = None
    error_message: Optional[str] = None


async def refresh_claim(
    ai_db: Any,
    db: Any,
    claim_id: int,
    source_event_type: str,
) -> ClaimRefreshResult:
    """Fetch source data, build projection, and persist for a single claim.

    This is the shared single-claim refresh algorithm used by both the
    historical backfill (Phase 4) and the change-stream listener (Phase 5).

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (writes).
        claim_id: the claim ID to refresh.
        source_event_type: ``"insert"`` / ``"update"`` / ``"replace"`` /
            ``"backfill"`` — recorded on dead-letters for traceability.

    Returns:
        A ``ClaimRefreshResult`` with the outcome. Never raises for data-level
        errors — those are dead-lettered and reflected in the result.

    Raises:
        ``asyncio.CancelledError``: if the caller is cancelled mid-refresh.
            Propagates immediately without dead-lettering.

    Side effects:
        - Upserts a projection into ``ai_invoice_analytics`` (if successful).
        - Records a dead-letter in ``ai_analytics_worker_dead_letters`` if
          the claim fails processing or has no source data.
        - Increments ``worker_metrics`` counters (``claims_refreshed``,
          ``projections_created`` / ``projections_updated``,
          ``claim_refresh_errors``, ``dead_letters_created``) so the Phase 8
          ``/status`` endpoint reports real throughput.
    """
    try:
        # Fetch source data (with timeout/retry from Phase 2).
        ai_line_items = await get_ai_line_items_for_claim_with_retry(
            ai_db, claim_id
        )
        conversations = await get_agent_conversations_for_claim_with_retry(
            ai_db, claim_id
        )

        # Build the projection (pure function from Phase 3).
        worker_processed_at = datetime.now(UTC)
        projection = build_projection(
            claim_id=claim_id,
            ai_line_items=ai_line_items,
            conversations=conversations,
            worker_processed_at=worker_processed_at,
        )

        if projection is None:
            # Nothing to project (no claim_id and no source) — dead-letter.
            await record_dead_letter(
                db,
                claim_id=claim_id,
                source_event_type=source_event_type,
                error_type="NoSourceData",
                error_message="build_projection returned None — no source data.",
            )
            worker_metrics.increment("dead_letters_created")
            return ClaimRefreshResult(
                outcome=OUTCOME_NO_SOURCE,
                claim_id=claim_id,
                error_type="NoSourceData",
                error_message="build_projection returned None — no source data.",
            )

        # Persist the projection.
        outcome = await upsert_projection(db, projection)
        resolved_claim_id = projection.get("_id", claim_id)
        # Counted once per successful refresh regardless of insert-vs-update,
        # so claims_refreshed == projections_created + projections_updated.
        worker_metrics.increment("claims_refreshed")
        if outcome == "inserted":
            worker_metrics.increment("projections_created")
            logger.debug(
                "Claim refresh: inserted projection for claim_id=%s "
                "(source_event=%s).",
                resolved_claim_id,
                source_event_type,
            )
            return ClaimRefreshResult(
                outcome=OUTCOME_INSERTED,
                claim_id=resolved_claim_id,
            )
        worker_metrics.increment("projections_updated")
        logger.debug(
            "Claim refresh: updated projection for claim_id=%s "
            "(source_event=%s).",
            resolved_claim_id,
            source_event_type,
        )
        return ClaimRefreshResult(
            outcome=OUTCOME_UPDATED,
            claim_id=resolved_claim_id,
        )

    except asyncio.CancelledError:
        # External cancellation (e.g. lifespan shutdown) — propagate
        # immediately. Do not dead-letter.
        logger.info(
            "Claim refresh cancelled (claim_id=%d, source_event=%s).",
            claim_id,
            source_event_type,
        )
        raise
    except Exception as exc:
        # Any other error: dead-letter the claim and return a result.
        # The caller (backfill or change-stream listener) continues with
        # the next claim — one bad claim never stops the worker.
        error_type = type(exc).__name__
        error_message = str(exc)
        await record_dead_letter(
            db,
            claim_id=claim_id,
            source_event_type=source_event_type,
            error_type=error_type,
            error_message=error_message,
        )
        worker_metrics.increment("claim_refresh_errors")
        worker_metrics.increment("dead_letters_created")
        logger.warning(
            "Claim refresh: claim_id=%d failed (source_event=%s, "
            "error_type=%s); dead-lettered.",
            claim_id,
            source_event_type,
            error_type,
        )
        return ClaimRefreshResult(
            outcome=OUTCOME_DEAD_LETTERED,
            claim_id=claim_id,
            error_type=error_type,
            error_message=error_message,
        )
