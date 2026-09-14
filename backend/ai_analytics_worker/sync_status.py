"""AI Analytics Worker — sync status aggregation (Phase 11).

Aggregates the worker's health state, sync integrity state, and metrics
into a single sync status that the dashboard frontend can display. This
is the backend equivalent of FireSquirrel's ``useSyncStatus`` — a single
derived status that tells the user whether the cache is in sync with
MongoDB.

Status values (stable, do not rename — the frontend matches on these):

- ``synced`` — worker running, last integrity check passed, no divergence.
  The cache matches MongoDB. Green.
- ``syncing`` — worker actively processing claims (queue has items,
  change stream events flowing). The cache is being kept up to date. Blue.
- ``catching-up`` — backfill in progress or large divergence being
  recovered (divergent_count > 0 and auto-resync enqueued). The cache
  is catching up to MongoDB. Yellow.
- ``divergence-detected`` — integrity check found mismatch but auto-resync
  has not yet reduced it. Orange. This is transient — it should move to
  ``catching-up`` then ``synced`` as the queue drains.
- ``error`` — worker in error state OR integrity check failed fatally.
  Operator attention needed. Red.
- ``stopped`` — worker disabled (``AI_ANALYTICS_WORKER_ENABLED=false``) or
  not running in this process. Grey. Legacy ``status`` field only.
- ``unknown`` — no local verification is available. The worker may be
  disabled, starting, or running in another process. Grey. ``data_status``
  field only.

The legacy ``status`` field keeps its original derivation (including
``stopped``) for existing consumers. New clients should read ``data_status``
(projection data status) and ``worker_availability`` (worker lifecycle) so a
lifecycle state is never confused with the projection's data status.

The status is derived from existing singletons (``worker_health``,
``sync_integrity_state``, ``worker_metrics``) — no new state is tracked
here. This keeps the status consistent with the actual worker state
without a separate source of truth.

Source: ``worker_health``, ``sync_integrity_state``, ``worker_metrics``
(read-only).
Destination: none (returns a dict for serialization).
Architectural constraints: no I/O, no async. Pure derivation.
"""

from __future__ import annotations

from typing import Any, Dict

from .config import worker_config
from .health import (
    STATUS_ERROR,
    STATUS_RECONCILING,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
    worker_health,
)
from .metrics import worker_metrics
from .sync_integrity import sync_integrity_state


# Sync status values — stable, do not rename (frontend matches on these).
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_SYNCING = "syncing"
SYNC_STATUS_CATCHING_UP = "catching-up"
SYNC_STATUS_DIVERGENCE_DETECTED = "divergence-detected"
SYNC_STATUS_ERROR = "error"
SYNC_STATUS_STOPPED = "stopped"
SYNC_STATUS_UNKNOWN = "unknown"


def derive_legacy_sync_status() -> str:
    """Derive the legacy ``status`` value from process-local state only.

    Derivation order (first match wins):
    1. Worker disabled → ``stopped``
    2. Worker in error → ``error``
    3. Integrity check failed → ``error``
    4. Integrity check in progress → ``syncing``
    5. Divergent claims found → ``catching-up``
    6. Count mismatch but no divergent samples yet → ``divergence-detected``
    7. Worker reconciling → ``syncing``
    8. Worker running → ``synced``
    9. Worker starting → ``syncing``
    10. Worker stopped (enabled but not started) → ``stopped``
    """
    if not worker_config.enabled:
        return SYNC_STATUS_STOPPED

    health_status = worker_health.status

    if health_status == STATUS_ERROR:
        return SYNC_STATUS_ERROR
    if sync_integrity_state.last_error is not None:
        return SYNC_STATUS_ERROR
    if sync_integrity_state.check_in_progress:
        return SYNC_STATUS_SYNCING
    if sync_integrity_state.divergent_count > 0:
        return SYNC_STATUS_CATCHING_UP
    if sync_integrity_state.count_mismatch:
        return SYNC_STATUS_DIVERGENCE_DETECTED
    if health_status == STATUS_RECONCILING:
        return SYNC_STATUS_SYNCING
    if health_status == STATUS_RUNNING:
        return SYNC_STATUS_SYNCED
    if health_status == STATUS_STARTING:
        return SYNC_STATUS_SYNCING
    return SYNC_STATUS_STOPPED


def derive_sync_status(*, deployment_worker_active: bool = False) -> str:
    """Derive the projection data status from worker health + integrity state.

    ``worker_health`` and ``sync_integrity_state`` are process-local, while
    the worker runs under a deployment-wide MongoDB leader lease. When this
    process is a stopped candidate and another process holds the lease, the
    local integrity state is stale (it belongs to a previous leadership term)
    and must not be reported as the deployment's data status.

    Derivation order (first match wins):
    1. Local worker stopped while another process holds the lease → ``unknown``
    2. Worker error or integrity failure → ``error``
    3. Integrity check in progress → ``syncing``
    4. Divergent claims found → ``catching-up``
    5. Count mismatch without samples → ``divergence-detected``
    6. Worker reconciling → ``syncing``
    7. Worker running after a successful verification → ``synced``
    8. No completed verification available → ``unknown``
    """
    health_status = worker_health.status

    if deployment_worker_active and health_status == STATUS_STOPPED:
        return SYNC_STATUS_UNKNOWN

    if health_status == STATUS_ERROR:
        return SYNC_STATUS_ERROR

    # Integrity check failed fatally.
    if sync_integrity_state.last_error is not None:
        return SYNC_STATUS_ERROR

    # Integrity check in progress — we're verifying.
    if sync_integrity_state.check_in_progress:
        return SYNC_STATUS_SYNCING

    # Divergent claims found and enqueued for resync — catching up.
    if sync_integrity_state.divergent_count > 0:
        return SYNC_STATUS_CATCHING_UP

    # Count mismatch but sample verification hasn't found specific
    # divergent claims yet (e.g. the mismatch is in older docs not in
    # the sample). Flag as divergence-detected so the operator knows
    # something is off.
    if sync_integrity_state.count_mismatch:
        return SYNC_STATUS_DIVERGENCE_DETECTED

    if health_status == STATUS_RECONCILING:
        return SYNC_STATUS_SYNCING

    if health_status == STATUS_RUNNING and sync_integrity_state.last_check_at is not None:
        return SYNC_STATUS_SYNCED

    if health_status == STATUS_STARTING:
        return SYNC_STATUS_SYNCING

    # A candidate process has no local verification state. The lease only
    # proves that another process is active; it does not prove the projection
    # is currently synchronized.
    return SYNC_STATUS_UNKNOWN


def sync_health_snapshot(
    *, deployment_worker_active: bool = False
) -> Dict[str, Any]:
    """Build the sync health dict for the /sync-health endpoint.

    Returns a dict with:
    - ``status``: legacy process-local sync status (includes ``stopped``).
    - ``data_status``: projection data status (includes ``unknown``).
    - ``worker_enabled``: whether the worker is enabled.
    - ``worker_status``: the worker health status, deployment-aware.
    - ``worker_availability``: worker lifecycle summary.
    - ``sync_integrity``: the integrity state snapshot.
    - ``metrics``: relevant throughput counters.
    - ``last_error``: the last error (from health or integrity), if any.

    This is the payload the dashboard frontend's SyncHealthIndicator
    consumes. It's auth-protected (mounted under /api/ai-analytics/).
    """
    integrity = sync_integrity_state.snapshot()
    metrics = worker_metrics.snapshot()

    # Pick the most recent error between health and integrity.
    last_error = worker_health.last_error or sync_integrity_state.last_error
    effective_worker_status = worker_health.status
    if (
        deployment_worker_active
        and worker_config.enabled
        and effective_worker_status == STATUS_STOPPED
    ):
        # This process is a healthy candidate; another process owns the lease.
        effective_worker_status = STATUS_RUNNING

    data_status = derive_sync_status(
        deployment_worker_active=deployment_worker_active
    )
    if not worker_config.enabled:
        worker_availability = "disabled"
    elif effective_worker_status == STATUS_ERROR:
        worker_availability = "error"
    elif effective_worker_status == STATUS_STARTING:
        worker_availability = "starting"
    elif deployment_worker_active or effective_worker_status in {
        STATUS_RUNNING,
        STATUS_RECONCILING,
    }:
        worker_availability = "active"
    else:
        worker_availability = "unavailable"

    return {
        "status": derive_legacy_sync_status(),
        "data_status": data_status,
        "worker_enabled": worker_config.enabled,
        "worker_status": effective_worker_status,
        "worker_availability": worker_availability,
        "last_started_at": worker_health.last_started_at,
        "last_successful_event_at": worker_health.last_successful_event_at,
        "last_checkpoint_at": worker_health.last_checkpoint_at,
        "consecutive_error_count": worker_health.consecutive_error_count,
        "sync_integrity": integrity,
        "metrics": {
            "events_received": metrics.get("events_received", 0),
            "claims_refreshed": metrics.get("claims_refreshed", 0),
            "projections_created": metrics.get("projections_created", 0),
            "projections_updated": metrics.get("projections_updated", 0),
            "dead_letters_created": metrics.get("dead_letters_created", 0),
            "sync_integrity_checks": metrics.get("sync_integrity_checks", 0),
            "sync_integrity_divergent_found": metrics.get(
                "sync_integrity_divergent_found", 0
            ),
        },
        "last_error": last_error,
    }
