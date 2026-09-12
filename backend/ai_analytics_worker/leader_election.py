"""AI Analytics Worker — leader election for multi-process deployments.

The backend runs under ``uvicorn --workers N`` (N=4), so each process boots
its own copy of the worker via the FastAPI lifespan. Without coordination
that means N duplicate change streams, reconciliation loops, and sync
integrity checks — wasted Atlas load and conflicting worker-state writes.

This module elects exactly one leader per deployment using a lease document
in the ``ai_analytics_worker_state`` collection::

    {"_id": "worker_leader",
     "holder_id": "<hostname>:<pid>:<rand>",
     "acquired_at": ...,
     "renewed_at": ...,
     "lease_expires_at": ...}

Acquisition is a single atomic ``find_one_and_update``: a process becomes
leader only if the lease is expired or already held by it. The leader renews
the lease periodically; if the leader process dies, the lease expires and a
campaigning peer takes over within ``lease + campaign`` seconds.

Why not ``asyncio.Lock`` or a file lock: those are per-process. Why not a
dedicated worker container: this keeps the deployment topology unchanged.

Source: none.
Destination: ``ai_analytics_worker_state`` (dashboard-owned MongoDB).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .config import worker_config

logger = logging.getLogger(__name__)


# Lock document id inside the worker-state collection. Distinct from the
# worker state doc (``_id == WORKER_NAME``) so health fields and the lease
# never collide.
LEADER_LOCK_ID = "worker_leader"


async def try_acquire_leadership(
    db: Any,
    holder_id: str,
    lease_seconds: float,
) -> bool:
    """Attempt to acquire (or re-acquire) the leader lease atomically.

    Succeeds when no lease document exists, the existing lease has expired,
    or the lease is already held by ``holder_id``. Fails cleanly when a
    different holder's lease is still valid, or when a concurrent upsert
    races the initial insert (DuplicateKeyError).

    Arguments:
        db: the dashboard-owned Motor database handle.
        holder_id: unique identifier for this process's candidacy.
        lease_seconds: how long the lease remains valid without renewal.

    Returns:
        True if this holder now owns the lease, False otherwise.
    """
    now = datetime.now(UTC)
    try:
        doc = await db[worker_config.WORKER_STATE_COLLECTION].find_one_and_update(
            {
                "_id": LEADER_LOCK_ID,
                "$or": [
                    {"lease_expires_at": {"$lt": now}},
                    {"holder_id": holder_id},
                ],
            },
            {
                "$set": {
                    "holder_id": holder_id,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "renewed_at": now,
                },
                "$setOnInsert": {"acquired_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # Another process won the initial insert race.
        return False

    return doc is not None and doc.get("holder_id") == holder_id


async def renew_leadership(
    db: Any,
    holder_id: str,
    lease_seconds: float,
) -> bool:
    """Extend the lease while this process still holds it.

    The update filter includes ``holder_id`` so a renewal can never
    resurrect a lease another leader has taken over.

    Returns:
        True if the lease was renewed, False if it is no longer held.
    """
    now = datetime.now(UTC)
    result = await db[worker_config.WORKER_STATE_COLLECTION].update_one(
        {"_id": LEADER_LOCK_ID, "holder_id": holder_id},
        {
            "$set": {
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "renewed_at": now,
            }
        },
    )
    return result.matched_count == 1


async def release_leadership(db: Any, holder_id: str) -> None:
    """Expire the lease immediately so a peer can take over without
    waiting for the TTL. Only applied if this process still holds it."""
    await db[worker_config.WORKER_STATE_COLLECTION].update_one(
        {"_id": LEADER_LOCK_ID, "holder_id": holder_id},
        {"$set": {"lease_expires_at": datetime(1970, 1, 1, tzinfo=UTC)}},
    )
