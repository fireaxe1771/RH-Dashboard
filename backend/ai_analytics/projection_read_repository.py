"""Read-side adapter for the AI Analytics Worker projection (Phase 9).

When ``settings.AI_ANALYTICS_USE_PROJECTION`` is true, the analytics services
read AI-side fields from the worker's ``ai_invoice_analytics`` projection
collection (in the dashboard-owned Mongo) instead of issuing a per-request
``$in`` query against the operational RecoveryHub_AI Mongo cluster.

This module is the thin adapter between the projection's field names
(Section 9 of the Phase 0 plan) and the field names that
``normalization_core.build_normalized_record`` reads from a raw
``ai_line_items`` document. By mapping projection → ai_record shape, the
existing ``build_normalized_record`` is reused unchanged — the SQL-side
join (business_outcome, cancellation, process_logs) stays identical, and
only the AI-Mongo source is swapped.

Source: ``ai_invoice_analytics`` collection in the dashboard-owned Mongo
(``db_manager.db``, NOT ``db_manager.ai_db``).
Destination: none (read-only).
Architectural constraints:
-- Never reads from RecoveryHub_AI Mongo (the projection is the cache).
-- Returns dicts shaped like raw ``ai_line_items`` documents so
   ``build_normalized_record`` works without modification.
-- Claims with no projection are simply absent from the result dict —
   ``build_normalized_record`` handles ``ai_record=None`` gracefully.
-- ``thread_id`` and ``retry_thread_id`` are not stored in the projection
   (Section 9 does not include them). They are 0% populated in production
   per the Phase 0 audit, so their absence is semantically identical to
   the direct-read path returning ``None`` for them. Phase 10 may enrich
   the projection to carry them.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ai_analytics_worker.config import worker_config

logger = logging.getLogger(__name__)


# Projection field name → raw ai_line_items field name that
# ``build_normalized_record`` reads. Fields with the same name in both
# shapes (e.g. ``confidence_level``, ``is_billable``, ``billing_category``,
# ``line_items_save_to_rh_status``) don't need a mapping entry — the
# adapter copies them through directly.
_FIELD_MAP: Dict[str, str] = {
    # projection field → ai_record field name
    "ai_processing_status": "claim_processing_status",
    "agent_execution_status": "agent_exec_status",
    "ai_invoice_total": "invoice_total",
    "processing_duration_seconds": "processing_time_seconds",
}

# Fields that exist in the raw ai_line_items doc but are NOT in the
# projection. All are 0% populated in production per the Phase 0 audit,
# so their absence from the projection is semantically identical to the
# direct-read path returning None. Listed here so the adapter can set
# them explicitly to None rather than omitting them — this makes the
# adapted dict a complete stand-in for a raw ai_record.
_MISSING_FROM_PROJECTION: tuple[str, ...] = (
    "thread_id",
    "retry_thread_id",
)

# Fields that pass through unchanged (same name in both shapes). Listed
# explicitly so the adapter is self-documenting and a future schema
# change is caught here rather than silently breaking the cohort.
_PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "confidence_level",
    "is_billable",
    "billing_category",
    "line_items_save_to_rh_status",
)


def projection_to_ai_record(
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    """Map a projection document to the raw ``ai_line_items`` field shape.

    ``build_normalized_record`` reads specific fields from the
    ``ai_record`` argument (e.g. ``claim_processing_status``,
    ``agent_exec_status``, ``confidence_level``). The projection stores
    some of these under different names (e.g. ``ai_processing_status``
    instead of ``claim_processing_status``). This function renames them
    so the projection can be passed to ``build_normalized_record`` as a
    drop-in replacement for a raw ``ai_line_items`` document.

    Fields not in the projection (``thread_id``, ``retry_thread_id``)
    are set to ``None`` — they are 0% populated in production per the
    Phase 0 audit, so this matches the direct-read path's behaviour.

    Arguments:
        projection: a document from the ``ai_invoice_analytics``
            collection.

    Returns:
        A dict with the field names that ``build_normalized_record``
        expects from a raw ``ai_line_items`` document.
    """
    adapted: Dict[str, Any] = {}

    # Passthrough fields (same name in both shapes).
    for field in _PASSTHROUGH_FIELDS:
        adapted[field] = projection.get(field)

    # Renamed fields.
    for proj_field, ai_field in _FIELD_MAP.items():
        adapted[ai_field] = projection.get(proj_field)

    # Fields not in the projection — explicit None for completeness.
    for field in _MISSING_FROM_PROJECTION:
        adapted[field] = None

    return adapted


async def get_projection_records_for_claim_ids(
    db: Any,
    claim_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    """Fetch projection documents for a batch of claim IDs.

    Returns a dict keyed by ``claim_id`` → adapted ai_record dict (ready
    to pass to ``build_normalized_record``). Claims with no projection
    are absent from the result — ``build_normalized_record`` handles
    ``ai_record=None`` gracefully, so callers iterate the SQL cohort and
    look up each claim_id in this dict, same as the direct-read path.

    Arguments:
        db: the dashboard-owned Motor database handle (``db_manager.db``,
            NOT ``db_manager.ai_db``).
        claim_ids: the claim IDs to fetch projections for.

    Returns:
        Dict mapping ``claim_id`` (int) → adapted ai_record dict.
    """
    if not claim_ids:
        return {}

    collection = db[worker_config.PROJECTIONS_COLLECTION]

    try:
        cursor = collection.find(
            {"_id": {"$in": claim_ids}},
        )
        docs = await cursor.to_list(length=len(claim_ids))
    except Exception as e:
        logger.error(
            "Projection read failed for %d claim_ids: %s", len(claim_ids), e
        )
        raise

    by_claim: Dict[int, Dict[str, Any]] = {}
    for doc in docs:
        # The projection's _id is the integer claim_id (Section 9.1).
        claim_id = doc.get("_id")
        if claim_id is None:
            continue
        try:
            cid = int(claim_id)
        except (ValueError, TypeError):
            continue
        by_claim[cid] = projection_to_ai_record(doc)

    logger.info(
        "Projection read: fetched %d/%d projections from ai_invoice_analytics.",
        len(by_claim),
        len(claim_ids),
    )
    return by_claim
