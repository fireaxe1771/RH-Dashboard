"""AI Analytics Worker — sync integrity verification (Phase 11).

Verifies that the projection cache matches the source MongoDB. This is the
"full reconciliation pull" equivalent from FireSquirrel's local-first sync
pattern: instead of assuming change streams + reconciliation are catching
everything, we periodically *prove* the cache is in sync.

Two checks run on each cycle:

1. **Count comparison** — ``ai_line_items.count()`` vs
   ``ai_invoice_analytics.count()``. A mismatch means projections are
   missing (cache has fewer) or stale tombstones exist (cache has more).

2. **Sample verification** — pick the N most recent ``ai_line_items``
   documents (by ``updated_at`` descending) and compare each source
   ``updated_at`` against the projection's ``source_latest_updated_at``.
   If the source is newer, the projection is divergent — the cache
   doesn't match MongoDB for that claim.

Divergent claims are automatically re-enqueued into the ``ClaimQueue``
for refresh (auto-resync), mirroring FireSquirrel's auto-re-pull on
divergence detection. Missing projections (source exists, no projection)
are also enqueued.

Results are stored in the in-memory ``sync_integrity_state`` singleton
and exposed via ``/status`` and ``/sync-health``.

Why this is separate from reconciliation (Phase 7):
- Reconciliation catches *missed change events* — it looks for source
  docs with ``updated_at > checkpoint``. It does NOT verify existing
  projections match their source.
- Sync integrity catches *divergence* — it verifies existing projections
  are correct by comparing them against the source. This catches direct
  Mongo edits that bypass the change stream, projection corruption, or
  backfill gaps that reconciliation wouldn't find because the
  ``updated_at`` is old.

Both are needed. Reconciliation is the "did I miss any events?" check.
Sync integrity is the "is the cache actually correct?" check.

Source: RecoveryHub_AI MongoDB (read-only — counts and samples
``ai_line_items``) + dashboard-owned MongoDB (counts and reads
``ai_invoice_analytics``).
Destination: enqueues divergent claim_ids into ``ClaimQueue`` (in-memory).
Architectural constraints:
-- Never writes to RecoveryHub_AI.
-- Cancellable via ``stop_event``.
-- Never raises — a failed check records the error and retries next cycle.
-- Count comparison is approximate during active writes (a claim may be
   counted on one side before the other catches up). The sample
   verification is authoritative — if a sample is divergent, it IS
   divergent regardless of count timing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from ai_analytics.mongo_repository import AI_LINE_ITEMS_COLLECTION

from .config import worker_config
from .metrics import worker_metrics

logger = logging.getLogger(__name__)

# Only the fields we need from the source and projection for comparison.
_SOURCE_SAMPLE_PROJECTION = {"_id": 1, "claim_id": 1, "updated_at": 1}
_PROJECTION_SAMPLE_PROJECTION = {
    "_id": 1,
    "source_latest_updated_at": 1,
    "has_ai_line_item_record": 1,
}


@dataclass
class SyncIntegrityResult:
    """Statistics returned by ``run_sync_integrity_once``.

    Attributes:
        source_count: total documents in ``ai_line_items``.
        projection_count: total documents in ``ai_invoice_analytics``.
        count_mismatch: True if source_count != projection_count.
        samples_checked: number of recent source docs sample-verified.
        divergent_claims: list of claim_ids where source updated_at >
            projection source_latest_updated_at (or projection missing).
        missing_projections: claim_ids in the sample with no projection at all.
        claims_enqueued: number of divergent/missing claims enqueued for refresh.
        started_at: when the check started.
        completed_at: when the check completed (None if cancelled/failed).
        cancelled: True if cancelled via stop_event.
        error: error message if the check failed fatally, else None.
    """

    source_count: int = 0
    projection_count: int = 0
    count_mismatch: bool = False
    samples_checked: int = 0
    divergent_claims: List[int] = field(default_factory=list)
    missing_projections: List[int] = field(default_factory=list)
    claims_enqueued: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: Optional[datetime] = None
    cancelled: bool = False
    error: Optional[str] = None


class SyncIntegrityState:
    """In-memory sync integrity state, exposed via /status and /sync-health.

    Holds the most recent integrity check result so the dashboard can show
    "sync is healthy" or "3 divergent claims being resynced" without
    re-running the check on every page load.

    Lifecycle: a single instance is created at module import as
    ``sync_integrity_state`` and lives for the lifetime of the process.
    """

    def __init__(self) -> None:
        self._last_check_at: Optional[datetime] = None
        self._source_count: int = 0
        self._projection_count: int = 0
        self._count_mismatch: bool = False
        self._divergent_count: int = 0
        self._missing_count: int = 0
        self._last_error: Optional[str] = None
        self._check_in_progress: bool = False

    def update_from_result(self, result: SyncIntegrityResult) -> None:
        """Update state from a completed integrity check result."""
        self._last_check_at = result.completed_at or result.started_at
        self._source_count = result.source_count
        self._projection_count = result.projection_count
        self._count_mismatch = result.count_mismatch
        self._divergent_count = len(result.divergent_claims)
        self._missing_count = len(result.missing_projections)
        self._last_error = result.error
        self._check_in_progress = False

    def mark_check_started(self) -> None:
        """Mark that an integrity check is in progress."""
        self._check_in_progress = True

    def record_error(self, error: str) -> None:
        """Record an error from a failed integrity check."""
        self._last_error = error
        self._check_in_progress = False

    def reset(self) -> None:
        """Reset all state to initial values (for tests)."""
        self._last_check_at = None
        self._source_count = 0
        self._projection_count = 0
        self._count_mismatch = False
        self._divergent_count = 0
        self._missing_count = 0
        self._last_error = None
        self._check_in_progress = False

    @property
    def last_check_at(self) -> Optional[datetime]:
        return self._last_check_at

    @property
    def source_count(self) -> int:
        return self._source_count

    @property
    def projection_count(self) -> int:
        return self._projection_count

    @property
    def count_mismatch(self) -> bool:
        return self._count_mismatch

    @property
    def divergent_count(self) -> int:
        return self._divergent_count

    @property
    def missing_count(self) -> int:
        return self._missing_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def check_in_progress(self) -> bool:
        return self._check_in_progress

    def snapshot(self) -> Dict[str, Any]:
        """Return a dict for serialization to /status and /sync-health."""
        return {
            "last_check_at": self._last_check_at,
            "check_in_progress": self._check_in_progress,
            "source_count": self._source_count,
            "projection_count": self._projection_count,
            "count_mismatch": self._count_mismatch,
            "divergent_count": self._divergent_count,
            "missing_count": self._missing_count,
            "last_error": self._last_error,
        }


# Single instance — imported by worker modules.
sync_integrity_state = SyncIntegrityState()


async def run_sync_integrity_once(
    ai_db: Any,
    db: Any,
    queue: Any,
    sample_size: Optional[int] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> SyncIntegrityResult:
    """Run a single sync integrity check and enqueue divergent claims.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (read-only here;
            writes happen via the queue consumer, not this function).
        queue: the ``ClaimQueue`` to enqueue divergent/missing claims into.
        sample_size: number of recent source docs to sample-verify.
            Defaults to ``worker_config.sync_integrity_sample_size``.
        stop_event: optional ``asyncio.Event`` for graceful cancellation.

    Returns:
        A ``SyncIntegrityResult`` with statistics about the check.

    Side effects:
        - Enqueues divergent/missing claim_ids into ``queue``.
        - Updates ``sync_integrity_state`` singleton.
    """
    if sample_size is None:
        sample_size = worker_config.sync_integrity_sample_size

    result = SyncIntegrityResult()
    sync_integrity_state.mark_check_started()
    worker_metrics.increment("sync_integrity_checks")

    try:
        # --- 1. Count comparison -----------------------------------------
        source_count = await ai_db[AI_LINE_ITEMS_COLLECTION].count_documents({})
        projection_count = await db[worker_config.PROJECTIONS_COLLECTION].count_documents({})

        if stop_event is not None and stop_event.is_set():
            result.cancelled = True
            result.completed_at = datetime.now(UTC)
            sync_integrity_state.update_from_result(result)
            return result

        result.source_count = source_count
        result.projection_count = projection_count
        result.count_mismatch = source_count != projection_count

        if result.count_mismatch:
            logger.info(
                "Sync integrity: count mismatch (source=%d, projection=%d). "
                "Sample verification will identify specific divergent claims.",
                source_count,
                projection_count,
            )

        # --- 2. Sample verification --------------------------------------
        # Fetch the N most recent source documents by updated_at descending.
        # This catches recent divergence reliably — older divergence is
        # caught by the count mismatch or by a future backfill.
        source_cursor = (
            ai_db[AI_LINE_ITEMS_COLLECTION]
            .find({}, _SOURCE_SAMPLE_PROJECTION)
            .sort("updated_at", -1)
            .limit(sample_size)
        )
        source_samples = await source_cursor.to_list(length=sample_size)

        if stop_event is not None and stop_event.is_set():
            result.cancelled = True
            result.completed_at = datetime.now(UTC)
            sync_integrity_state.update_from_result(result)
            return result

        result.samples_checked = len(source_samples)

        # Extract claim_ids and build a set of (claim_id → source updated_at).
        source_by_claim: Dict[int, Any] = {}
        for doc in source_samples:
            raw_claim_id = doc.get("claim_id")
            if raw_claim_id is None:
                continue
            try:
                cid = int(raw_claim_id)
            except (ValueError, TypeError):
                continue
            source_by_claim[cid] = doc.get("updated_at")

        if not source_by_claim:
            # No valid claim_ids in the sample — nothing to verify.
            result.completed_at = datetime.now(UTC)
            sync_integrity_state.update_from_result(result)
            return result

        # Fetch the corresponding projections by _id (which is claim_id).
        claim_ids = list(source_by_claim.keys())
        proj_cursor = db[worker_config.PROJECTIONS_COLLECTION].find(
            {"_id": {"$in": claim_ids}},
            _PROJECTION_SAMPLE_PROJECTION,
        )
        proj_docs = await proj_cursor.to_list(length=len(claim_ids))

        # Build a map of claim_id → projection source_latest_updated_at.
        proj_by_claim: Dict[int, Any] = {}
        for pdoc in proj_docs:
            pid = pdoc.get("_id")
            if pid is None:
                continue
            try:
                cid = int(pid)
            except (ValueError, TypeError):
                continue
            proj_by_claim[cid] = pdoc.get("source_latest_updated_at")

        # Compare: if source updated_at > projection source_latest_updated_at,
        # the projection is divergent. If no projection exists, it's missing.
        for cid, source_updated_at in source_by_claim.items():
            if cid not in proj_by_claim:
                result.missing_projections.append(cid)
                result.divergent_claims.append(cid)
            else:
                proj_updated_at = proj_by_claim[cid]
                if source_updated_at is not None and (
                    proj_updated_at is None
                    or _datetime_gt(source_updated_at, proj_updated_at)
                ):
                    result.divergent_claims.append(cid)

        # --- 3. Auto-resync: enqueue divergent claims --------------------
        if result.divergent_claims:
            worker_metrics.increment(
                "sync_integrity_divergent_found",
                len(result.divergent_claims),
            )
            for cid in result.divergent_claims:
                queue.enqueue(cid)
                result.claims_enqueued += 1
            logger.info(
                "Sync integrity: enqueued %d divergent claims for refresh "
                "(%d missing, %d stale).",
                result.claims_enqueued,
                len(result.missing_projections),
                len(result.divergent_claims) - len(result.missing_projections),
            )
        else:
            logger.debug(
                "Sync integrity: all %d sampled projections match source.",
                result.samples_checked,
            )

        result.completed_at = datetime.now(UTC)
        sync_integrity_state.update_from_result(result)

    except asyncio.CancelledError:
        result.cancelled = True
        result.completed_at = datetime.now(UTC)
        sync_integrity_state.update_from_result(result)
        raise
    except Exception as exc:
        result.error = str(exc)
        result.completed_at = datetime.now(UTC)
        sync_integrity_state.record_error(result.error)
        logger.error("Sync integrity check failed: %s", exc)
        # Don't re-raise — a failed check retries on the next cycle.
        # The error is visible in /status and /sync-health.

    return result


async def run_sync_integrity_loop(
    ai_db: Any,
    db: Any,
    stop_event: asyncio.Event,
    queue: Any,
    interval_minutes: Optional[int] = None,
    sample_size: Optional[int] = None,
) -> None:
    """Run periodic sync integrity checks until ``stop_event`` is set.

    Sleeps for ``interval_minutes`` between checks. The first check runs
    immediately on startup (to catch divergence from a prior crash),
    then subsequent checks run on the configured interval.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (read-only here).
        stop_event: an ``asyncio.Event`` that the caller sets to request
            graceful shutdown.
        queue: the ``ClaimQueue`` to enqueue divergent claims into.
        interval_minutes: minutes between checks. Defaults to
            ``worker_config.sync_integrity_interval_minutes``.
        sample_size: number of recent source docs to sample-verify per
            check. Defaults to ``worker_config.sync_integrity_sample_size``.

    Raises:
        ``asyncio.CancelledError``: if the caller cancels the task.
        Other exceptions are logged and the loop continues — a transient
        failure in one check should not stop future checks.
    """
    if interval_minutes is None:
        interval_minutes = worker_config.sync_integrity_interval_minutes
    if sample_size is None:
        sample_size = worker_config.sync_integrity_sample_size

    interval_seconds = interval_minutes * 60

    logger.info(
        "Sync integrity loop started (interval=%dm, sample_size=%d).",
        interval_minutes,
        sample_size,
    )

    while not stop_event.is_set():
        try:
            await run_sync_integrity_once(
                ai_db=ai_db,
                db=db,
                queue=queue,
                sample_size=sample_size,
                stop_event=stop_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Sync integrity check failed (error_type=%s); will retry "
                "on next interval.",
                type(exc).__name__,
            )

        if stop_event.is_set():
            break

        # Sleep until the next check, checking stop_event periodically.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass  # Interval elapsed — run next check
        except asyncio.CancelledError:
            raise

    logger.info("Sync integrity loop: stopped via stop_event.")


def _datetime_gt(a: Any, b: Any) -> bool:
    """Compare two datetime-like values, returning True if ``a > b``.

    Handles timezone-naive vs timezone-aware datetimes by normalizing both
    to UTC. Motor may return naive UTC datetimes depending on the driver
    version, so we can't assume tzinfo is present.
    """
    a_norm = _normalize_dt(a)
    b_norm = _normalize_dt(b)
    if a_norm is None or b_norm is None:
        return False
    return a_norm > b_norm


def _normalize_dt(dt: Any) -> Optional[datetime]:
    """Normalize a datetime to timezone-aware UTC, or return None."""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
