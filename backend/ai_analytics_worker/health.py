"""AI Analytics Worker — in-memory health state.

Tracks the worker's runtime status, lifecycle timestamps, and error counters
in memory. The field shape mirrors the persisted ``ai_analytics_worker_state``
document (Section 10.2 of the Phase 0 plan) so the in-memory state can be
serialized to that document verbatim when persistence is added in Phase 4.

Source: none (in-memory only).
Destination: none directly — Phase 4's ``projection_repository`` will persist
``snapshot()`` to the ``ai_analytics_worker_state`` collection.
Architectural constraints: no I/O. All timestamps are timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Optional

from .config import worker_config


# Worker status values — must match Section 10.2 ``status`` field.
# Kept here (not in config.py) because they are behavioral state values, not
# environment configuration.
STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_RECONCILING = "reconciling"
STATUS_ERROR = "error"

_ALL_STATUSES = {
    STATUS_STOPPED, STATUS_STARTING, STATUS_RUNNING,
    STATUS_RECONCILING, STATUS_ERROR,
}


class WorkerHealth:
    """In-memory worker health state.

    Responsibility: hold the worker's current status, lifecycle timestamps,
    and error counters in a single mutable object that worker modules update
    and health endpoints read.

    Lifecycle: a single instance is created at module import as
    ``worker_health`` and lives for the lifetime of the process.

    Inputs: ``worker_config`` (for worker_version and projection_schema_version
    stamped on the snapshot).
    Outputs: ``snapshot()`` returns a dict shaped like the
    ``ai_analytics_worker_state`` document (Section 10.2).
    Dependencies: ``worker_config`` (read-only).
    Error behavior: setters validate status values and raise ``ValueError`` on
    unknown status; timestamp setters accept ``None`` to clear a field.
    """

    def __init__(self) -> None:
        self._status: str = STATUS_STOPPED
        self._worker_version: str = worker_config.worker_version
        self._projection_schema_version: int = worker_config.projection_schema_version
        self._last_started_at: Optional[datetime] = None
        self._last_completed_at: Optional[datetime] = None
        self._last_successful_event_at: Optional[datetime] = None
        self._last_checkpoint_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._consecutive_error_count: int = 0

    # --- Status ------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    def set_status(self, status: str) -> None:
        """Set the worker status.

        Raises:
            ValueError: if ``status`` is not one of the known status values.
        """
        if status not in _ALL_STATUSES:
            raise ValueError(
                f"Unknown worker status: {status!r}. "
                f"Expected one of {sorted(_ALL_STATUSES)}."
            )
        self._status = status

    # --- Lifecycle timestamps ----------------------------------------------

    def mark_started(self) -> None:
        """Record that the worker has started (sets last_started_at to now)."""
        self._last_started_at = datetime.now(UTC)
        self._consecutive_error_count = 0
        self._last_error = None

    def mark_completed(self) -> None:
        """Record that the worker has completed a clean cycle."""
        self._last_completed_at = datetime.now(UTC)

    def mark_successful_event(self) -> None:
        """Record that the worker processed an event successfully."""
        self._last_successful_event_at = datetime.now(UTC)

    def mark_checkpoint(self) -> None:
        """Record that the worker persisted a resume token / checkpoint."""
        self._last_checkpoint_at = datetime.now(UTC)

    # --- Error tracking ----------------------------------------------------

    def record_error(self, error: str) -> None:
        """Record a worker error and increment the consecutive error count."""
        self._last_error = error
        self._consecutive_error_count += 1
        self._status = STATUS_ERROR

    def clear_error(self) -> None:
        """Clear the last error and reset the consecutive error count."""
        self._last_error = None
        self._consecutive_error_count = 0

    def reset(self) -> None:
        """Reset all health state to initial values.

        Used by tests to start each test from a clean slate. Not intended for
        production use — the worker tracks cumulative state since process start.
        """
        self._status = STATUS_STOPPED
        self._last_started_at = None
        self._last_completed_at = None
        self._last_successful_event_at = None
        self._last_checkpoint_at = None
        self._last_error = None
        self._consecutive_error_count = 0

    # --- Read-only accessors -----------------------------------------------

    @property
    def last_started_at(self) -> Optional[datetime]:
        return self._last_started_at

    @property
    def last_completed_at(self) -> Optional[datetime]:
        return self._last_completed_at

    @property
    def last_successful_event_at(self) -> Optional[datetime]:
        return self._last_successful_event_at

    @property
    def last_checkpoint_at(self) -> Optional[datetime]:
        return self._last_checkpoint_at

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def consecutive_error_count(self) -> int:
        return self._consecutive_error_count

    @property
    def worker_version(self) -> str:
        return self._worker_version

    @property
    def projection_schema_version(self) -> int:
        return self._projection_schema_version

    # --- Serialization -----------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return a dict shaped like the ``ai_analytics_worker_state`` document.

        Phase 4's projection repository will upsert this dict into the
        ``ai_analytics_worker_state`` collection keyed by ``_id`` = worker name.
        Datetimes are returned as timezone-aware UTC objects (Motor stores
        them as BSON datetimes preserving the UTC offset).
        """
        return {
            "_id": worker_config.WORKER_NAME,
            "worker_version": self._worker_version,
            "projection_schema_version": self._projection_schema_version,
            "last_started_at": self._last_started_at,
            "last_completed_at": self._last_completed_at,
            "last_successful_event_at": self._last_successful_event_at,
            "last_checkpoint_at": self._last_checkpoint_at,
            "status": self._status,
            "last_error": self._last_error,
            "consecutive_error_count": self._consecutive_error_count,
        }


# Single instance — imported by worker modules as
# ``from ai_analytics_worker.health import worker_health``.
worker_health = WorkerHealth()
