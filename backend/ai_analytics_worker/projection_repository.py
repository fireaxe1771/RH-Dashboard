"""AI Analytics Worker — projection repository (Phase 4).

Persistence layer for the worker's destination collections in the
dashboard-owned MongoDB database (``settings.MONGODB_DB_NAME``). All writes
go here; the source repository is read-only.

Three concerns are persisted here:

1. **Projections** — ``ai_invoice_analytics`` collection. One document per
   claim, upserted by ``claim_id`` (the ``_id``). Idempotent: re-running a
   backfill or refresh for the same claim overwrites the prior projection
   rather than creating a duplicate.
2. **Worker runs** — ``ai_analytics_worker_runs`` collection. Audit log of
   batch processing cycles (backfill, reconciliation). Not written for
   individual claim refreshes — only for batch operations (Section 10.4).
3. **Worker state** — ``ai_analytics_worker_state`` collection. Single
   document per worker tracking synchronization state (resume token,
   checkpoint, status). Updated by the worker lifecycle, change-stream
   listener, and reconciliation.

Source: none (this module only writes).
Destination: ``ai_invoice_analytics``, ``ai_analytics_worker_runs``,
``ai_analytics_worker_state`` collections in the dashboard MongoDB.
Architectural constraints:
- Never writes to RecoveryHub_AI collections (source is read-only).
- Upsert semantics: a re-run of the same claim replaces, not duplicates.
- ``_id`` of a projection is the integer ``claim_id`` per Section 9.1.
  Projections with ``_id=None`` (claim_id derivation failed) are rejected
  with ``ValueError`` — the caller must dead-letter them instead.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from .config import worker_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Projection upsert
# ---------------------------------------------------------------------------


async def upsert_projection(db: Any, projection: Dict[str, Any]) -> str:
    """Upsert a single projection document into ``ai_invoice_analytics``.

    Arguments:
        db: the dashboard-owned Motor database handle.
        projection: the projection dict from ``projection_builder.build_projection``.
            Must have a non-None ``_id`` (the integer ``claim_id``).

    Returns:
        The string ``"inserted"`` if a new document was created, ``"updated"``
        if an existing document was replaced.

    Raises:
        ValueError: if ``projection["_id"]`` is None — the caller must
            dead-letter the claim instead of attempting to persist.
        DuplicateKeyError: if a concurrent write created the same ``_id``
            between the upsert's internal find and replace. The caller should
            retry (the second attempt will update).
    """
    projection_id = projection.get("_id")
    if projection_id is None:
        raise ValueError(
            "Cannot upsert a projection with _id=None (claim_id derivation "
            "failed). Dead-letter the claim instead."
        )

    collection = db[worker_config.PROJECTIONS_COLLECTION]

    # Use replace_one with upsert=True rather than update_one — the projection
    # is a complete document, not a partial update. This ensures stale fields
    # from a prior schema version are removed when the projection is rebuilt.
    result = await collection.replace_one(
        {"_id": projection_id},
        projection,
        upsert=True,
    )

    if result.upserted_id is not None:
        logger.debug(
            "Inserted projection for claim_id=%s (worker_version=%s).",
            projection_id,
            worker_config.worker_version,
        )
        return "inserted"

    logger.debug(
        "Updated projection for claim_id=%s (worker_version=%s).",
        projection_id,
        worker_config.worker_version,
    )
    return "updated"


# ---------------------------------------------------------------------------
# Worker run audit log
# ---------------------------------------------------------------------------


async def record_worker_run(db: Any, run_doc: Dict[str, Any]) -> ObjectId:
    """Insert a worker run audit record into ``ai_analytics_worker_runs``.

    Arguments:
        db: the dashboard-owned Motor database handle.
        run_doc: the run document conforming to Section 10.4. Must include
            ``run_type`` and ``started_at``. The ``_id`` is auto-generated.

    Returns:
        The inserted document's ``_id`` (ObjectId).
    """
    collection = db[worker_config.WORKER_RUNS_COLLECTION]
    result = await collection.insert_one(run_doc)
    logger.debug(
        "Recorded worker run (type=%s, _id=%s).",
        run_doc.get("run_type"),
        result.inserted_id,
    )
    return result.inserted_id


async def update_worker_run(
    db: Any,
    run_id: ObjectId,
    updates: Dict[str, Any],
) -> None:
    """Update a worker run record with completion stats or status.

    Arguments:
        db: the dashboard-owned Motor database handle.
        run_id: the ``_id`` returned by ``record_worker_run``.
        updates: fields to set (e.g. ``completed_at``, ``status``,
            ``claims_processed``).
    """
    collection = db[worker_config.WORKER_RUNS_COLLECTION]
    await collection.update_one({"_id": run_id}, {"$set": updates})


# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------


async def get_worker_state(db: Any, worker_name: str) -> Optional[Dict[str, Any]]:
    """Fetch the worker state document by worker name (``_id``).

    Arguments:
        db: the dashboard-owned Motor database handle.
        worker_name: the ``_id`` of the worker state document (e.g.
            ``worker_config.WORKER_NAME``).

    Returns:
        The state document, or ``None`` if no state exists yet (first run).
    """
    collection = db[worker_config.WORKER_STATE_COLLECTION]
    return await collection.find_one({"_id": worker_name})


async def update_worker_state(
    db: Any,
    worker_name: str,
    updates: Dict[str, Any],
) -> None:
    """Upsert fields on the worker state document.

    Creates the document if it doesn't exist (first run). Uses
    ``$set`` so partial updates don't clobber existing fields.

    Arguments:
        db: the dashboard-owned Motor database handle.
        worker_name: the ``_id`` of the worker state document.
        updates: fields to set (e.g. ``status``, ``last_checkpoint_at``).
    """
    collection = db[worker_config.WORKER_STATE_COLLECTION]
    await collection.update_one(
        {"_id": worker_name},
        {"$set": updates},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Dead-letter collection
# ---------------------------------------------------------------------------


async def record_dead_letter(
    db: Any,
    claim_id: Optional[int],
    source_event_type: str,
    error_type: str,
    error_message: str,
    attempt_count: int = 1,
    worker_version: Optional[str] = None,
) -> ObjectId:
    """Insert or update a dead-letter record for a failing claim.

    If a dead-letter record already exists for the same ``claim_id`` and
    ``resolved=False``, the ``last_failed_at`` and ``attempt_count`` are
    updated. Otherwise a new record is inserted.

    Arguments:
        db: the dashboard-owned Motor database handle.
        claim_id: the claim that failed (may be None if claim_id extraction
            failed).
        source_event_type: ``insert`` / ``update`` / ``replace`` / ``backfill``.
        error_type: exception class name.
        error_message: error message string.
        attempt_count: number of attempts before dead-lettering.
        worker_version: worker code version (defaults to config).

    Returns:
        The dead-letter document's ``_id`` (ObjectId).
    """
    if worker_version is None:
        worker_version = worker_config.worker_version

    collection = db[worker_config.DEAD_LETTERS_COLLECTION]
    now = datetime.now(UTC)

    # If an unresolved dead-letter exists for this claim, update it.
    if claim_id is not None:
        existing = await collection.find_one(
            {"claim_id": claim_id, "resolved": False}
        )
        if existing is not None:
            await collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "last_failed_at": now,
                        "error_type": error_type,
                        "error_message": error_message,
                        "attempt_count": attempt_count,
                        "worker_version": worker_version,
                    },
                },
            )
            return existing["_id"]

    # Insert a new dead-letter record.
    doc = {
        "claim_id": claim_id,
        "source_event_type": source_event_type,
        "error_type": error_type,
        "error_message": error_message,
        "first_failed_at": now,
        "last_failed_at": now,
        "attempt_count": attempt_count,
        "worker_version": worker_version,
        "resolved": False,
    }
    result = await collection.insert_one(doc)
    logger.warning(
        "Dead-lettered claim_id=%s (error_type=%s, attempts=%d).",
        claim_id,
        error_type,
        attempt_count,
    )
    return result.inserted_id
