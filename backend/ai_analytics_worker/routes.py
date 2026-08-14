"""AI Analytics Worker — health and operations endpoints (Phase 8).

Exposes the worker's in-memory health state and metrics counters via HTTP so
operators can monitor the worker without querying MongoDB directly. Three
endpoints are provided, each with a distinct purpose:

- ``/health`` — **liveness** probe. Returns 200 if the worker process is
  alive. Used by Azure Container Apps to decide whether to restart the
  container. Never returns 503 (a dead process can't return anything).
  Unauthenticated — probes must not require auth.
- ``/ready`` — **readiness** probe. Returns 200 only when the worker is
  enabled and actively working (``STATUS_RUNNING`` or
  ``STATUS_RECONCILING``). Returns 503 if the worker is disabled, in
  ``STATUS_ERROR``, or has not yet started (``STATUS_STOPPED`` /
  ``STATUS_STARTING``). Unauthenticated — probes must not require auth.
- ``/status`` — **operational dashboard**. Returns the full
  ``worker_health.snapshot()`` plus ``worker_metrics.snapshot()`` so
  operators can see lifecycle timestamps, error counts, and cumulative
  throughput counters. Auth-protected like other ``/api/*`` routes.

All endpoints are read-only on in-memory state — no MongoDB queries on the
hot path. The snapshot is already in memory (Phase 1 singletons), so the
endpoints never block the event loop.

Source: none (reads in-memory singletons ``worker_health`` and
``worker_metrics``).
Destination: none.
Architectural constraints:
- ``/health`` and ``/ready`` are unauthenticated (container probe contract),
  so neither may include error text, connection details, or any other
  internal identifier in its payload. The backend Container App has
  external ingress, making these responses world-readable.
- ``/status`` is auth-protected via ``get_current_user`` and is the only
  endpoint that exposes ``last_error``.
- Endpoints never block — they return the current in-memory snapshot
  synchronously.
- Datetimes in the response are ISO 8601 strings (FastAPI serializes
  timezone-aware datetimes correctly).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Response

from auth import get_current_user

from .config import worker_config
from .health import (
    STATUS_ERROR,
    STATUS_RECONCILING,
    STATUS_RUNNING,
    STATUS_STOPPED,
    worker_health,
)
from .metrics import worker_metrics

# Router mounted under the "/api/ai-analytics/worker" prefix in main.py.
# Tags group these endpoints in the OpenAPI docs.
worker_router = APIRouter(tags=["AI Analytics Worker"])

# Statuses that mean the worker is actively working and should receive
# traffic. ``STATUS_RECONCILING`` is included because a reconciliation scan
# is normal healthy operation, not a degraded state — reporting it as
# not-ready would pull the instance out of rotation on every scan.
# ``STATUS_STARTING`` is excluded: the worker has not yet reached a
# processing state.
_READY_STATUSES = frozenset({STATUS_RUNNING, STATUS_RECONCILING})


@worker_router.get("/health")
async def worker_health_probe() -> Dict[str, Any]:
    """Liveness probe — returns 200 if the worker process is alive.

    This endpoint exists for Azure Container Apps liveness checks. A 200
    means the FastAPI process is running and the event loop is responsive.
    It does NOT mean the worker is processing events — use ``/ready`` for
    that.

    Unauthenticated: container probes must not require auth tokens.
    """
    return {
        "status": "alive",
        "worker_enabled": worker_config.enabled,
    }


@worker_router.get("/ready")
async def worker_ready_probe(response: Response) -> Dict[str, Any]:
    """Readiness probe — returns 200 only when the worker is ready to serve.

    Returns 200 when the worker is enabled and its status is in
    ``_READY_STATUSES`` (``STATUS_RUNNING`` or ``STATUS_RECONCILING``).
    Returns 503 when:
    - The worker is disabled (``AI_ANALYTICS_WORKER_ENABLED=false``) — the
      endpoint is still mounted, but the worker isn't running, so traffic
      routing to the worker's processing path should not occur.
    - The worker is in ``STATUS_ERROR`` — a fatal error has occurred and
      operator attention is required.
    - The worker is in ``STATUS_STOPPED`` with no ``last_started_at`` — the
      worker has never started (e.g. startup backfill not yet complete, or
      the lifespan startup failed before spawning the worker task).

    Unauthenticated: container probes must not require auth tokens. Because
    this endpoint is reachable anonymously over the public ingress, the
    payload deliberately excludes ``last_error`` — see the comment on the
    ``STATUS_ERROR`` branch.
    """
    if not worker_config.enabled:
        response.status_code = 503
        return {
            "ready": False,
            "reason": "worker_disabled",
            "status": worker_health.status,
        }

    status = worker_health.status
    if status in _READY_STATUSES:
        return {
            "ready": True,
            "status": status,
            "last_started_at": worker_health.last_started_at,
            "last_checkpoint_at": worker_health.last_checkpoint_at,
        }

    if status == STATUS_ERROR:
        response.status_code = 503
        # Deliberately does NOT include ``last_error``. This endpoint is
        # unauthenticated and the backend Container App has external ingress,
        # so the payload is readable by anonymous callers on the internet.
        # ``record_error`` stores raw exception text, and a driver-level
        # failure string embeds the Atlas cluster hostname, port, and timeout
        # configuration. The error text is available on the auth-protected
        # ``/status`` endpoint instead. ``consecutive_error_count`` is a bare
        # integer and discloses nothing.
        return {
            "ready": False,
            "reason": "worker_error",
            "status": status,
            "consecutive_error_count": worker_health.consecutive_error_count,
        }

    # STATUS_STOPPED or STATUS_STARTING — not ready.
    # STATUS_STOPPED with last_started_at means the worker ran and shut down
    # cleanly (e.g. config reload); without it, the worker never started.
    response.status_code = 503
    reason = "worker_not_running" if status == STATUS_STOPPED else f"worker_{status}"
    return {
        "ready": False,
        "reason": reason,
        "status": status,
        "last_started_at": worker_health.last_started_at,
    }


@worker_router.get(
    "/status",
    dependencies=[Depends(get_current_user)],
)
async def worker_status() -> Dict[str, Any]:
    """Operational dashboard — full health snapshot plus metrics counters.

    Returns the complete in-memory state of the worker:
    - Lifecycle timestamps (started, completed, checkpoint, last successful
      event).
    - Current status and error tracking.
    - Cumulative throughput counters (events received, claims refreshed,
      projections created/updated, dead-letters, reconciliation runs, etc.).

    Auth-protected via ``get_current_user`` — this is an operational
    endpoint, not a container probe.

    The response is a single dict combining ``worker_health.snapshot()``
    and ``worker_metrics.snapshot()`` under separate keys so consumers can
    distinguish lifecycle state from throughput counters.
    """
    return {
        "enabled": worker_config.enabled,
        "health": worker_health.snapshot(),
        "metrics": worker_metrics.snapshot(),
    }
