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

Phase 1 (this package skeleton) introduces the package, worker config
accessor, in-memory health state, and metrics counters — no business logic.
Phase 2 adds the source repository wrapper (timeout, retry, structured
logging over the existing mongo_repository read functions).
Phase 3 adds the projection builder (deterministic ai_invoice_analytics
document construction from source data, reusing normalization_core.py).
Subsequent phases add persistence, backfill, change-stream listening,
and reconciliation.
"""

__all__ = ["config", "health", "metrics", "source_repository", "projection_builder"]
