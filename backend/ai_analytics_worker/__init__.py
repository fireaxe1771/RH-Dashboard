"""AI Analytics Worker — event-driven analytics projection service.

Watches RecoveryHub_AI MongoDB (read-only) for relevant changes, normalizes
AI-side data into a dashboard-oriented analytics projection, and writes that
projection into the dashboard-owned MongoDB database.

The worker lives in this subpackage of the existing backend and runs as a
background asyncio task within the FastAPI process (see
``backend/main.py`` lifespan handler). It shares ``config.py``, ``database.py``,
and the shared normalization module ``backend/ai_analytics/normalization_core.py``.

Architectural constraints (binding — see
docs/ai-analytics/PHASE_0_IMPLEMENTATION_PLAN.md Section 16):
- One-way only. Reads operational AI Mongo; never mutates it.
- Never mutates RecoveryHub SQL.
- Writes only to its analytics destination (dashboard-owned Mongo).
- Never becomes a required dependency for operational claim processing.
- Cancellable within 5 seconds for graceful FastAPI shutdown.

Phase 1 introduced the package skeleton, worker config accessor, in-memory
health state, and metrics counters.
Phase 2 added the source repository wrapper (timeout, retry, structured
logging over the existing mongo_repository read functions).
Phase 3 added the projection builder (deterministic ai_invoice_analytics
document construction from source data, reusing normalization_core.py).
Phase 4 added the projection repository (destination writes) and the
historical backfill orchestrator.
Phase 5 added the change-stream listener (near-real-time incremental
updates via MongoDB Change Streams with resume token persistence) and the
shared claim refresh algorithm.
Phase 6 added the claim deduplication/debounce queue and queue consumer,
decoupling event detection from projection rebuilds so bursts of updates
to the same claim coalesce into a single refresh.
Phase 7 added the safety-net reconciliation loop that periodically scans
``ai_line_items`` for claims updated since the last checkpoint and
enqueues them for refresh, covering events missed by the change stream
(oplog gaps, crash between enqueue and refresh, deletes).
Phase 8 added the health and operations endpoints (``/health``,
``/ready``, ``/status``) that expose the in-memory worker state and
metrics counters to operators and container probes.
Subsequent phases add RH-Dashboard integration, staleness UI, security
hardening, and deployment.
"""

__all__ = [
    "config",
    "health",
    "metrics",
    "source_repository",
    "projection_builder",
    "projection_repository",
    "claim_refresh",
    "backfill",
    "change_stream_listener",
    "queue",
    "reconciliation",
    "routes",
]
