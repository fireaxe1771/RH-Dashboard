"""Worker-specific configuration accessor (thin re-export).

The single source of truth for all runtime configuration is the ``Settings``
class in ``backend/config.py`` (DRY rule, Section 2.1.4). This module exposes
the worker-relevant subset via a small accessor object so worker modules import
``worker_config.WORKER_VERSION`` rather than reaching into the global
``settings`` object directly. No environment variable is read here — every
value is sourced from ``config.settings``.

Source: ``backend/config.py`` (read-only).
Destination: none.
Architectural constraints: never redefines a value that lives in ``config.py``.
"""

from __future__ import annotations

from config import settings


class WorkerConfig:
    """Read-only accessor over the worker-relevant ``Settings`` subset.

    Lifecycle: instantiated once at module import as ``worker_config``.
    Inputs: the global ``config.settings`` instance.
    Outputs: typed attributes consumed by worker modules.
    Dependencies: ``config.settings`` (must be importable).
    Error behavior: attribute access never raises — defaults come from
    ``Settings`` class attributes. Validation of these values happens in
    ``Settings.validate_settings()`` at startup.
    """

    @property
    def enabled(self) -> bool:
        """Whether the worker task should start in the FastAPI lifespan."""
        return settings.AI_ANALYTICS_WORKER_ENABLED

    @property
    def worker_version(self) -> str:
        """Worker code version stamped on projections and worker-state records."""
        return settings.AI_ANALYTICS_WORKER_VERSION

    @property
    def projection_schema_version(self) -> int:
        """Frozen projection schema version (Section 9.12 evolution policy)."""
        return settings.AI_ANALYTICS_WORKER_PROJECTION_SCHEMA_VERSION

    @property
    def debounce_seconds(self) -> float:
        """Coalescing debounce window in seconds (Phase 6/8)."""
        return settings.WORKER_DEBOUNCE_SECONDS

    @property
    def max_claims_per_cycle(self) -> int:
        """Max claims processed per worker cycle (event-loop starvation guard)."""
        return settings.WORKER_MAX_CLAIMS_PER_CYCLE

    @property
    def source_query_timeout_ms(self) -> int:
        """Per-source-query timeout in milliseconds (Phase 2)."""
        return settings.WORKER_SOURCE_QUERY_TIMEOUT_MS

    @property
    def reconciliation_interval_minutes(self) -> int:
        """Reconciliation safety-net cadence in minutes (Section 8.6)."""
        return settings.WORKER_RECONCILIATION_INTERVAL_MINUTES

    @property
    def backfill_batch_size(self) -> int:
        """Backfill batch size for historical population (Phase 4/6)."""
        return settings.WORKER_BACKFILL_BATCH_SIZE

    @property
    def max_retries(self) -> int:
        """Max total attempts (including initial) before dead-letter (Phase 5).

        Note: despite the name, this is the max total attempts, not retries
        after the first attempt. A value of 3 means 3 total attempts
        (1 initial + 2 retries). See ``config.WORKER_MAX_RETRIES``.
        """
        return settings.WORKER_MAX_RETRIES

    @property
    def dead_letter_threshold(self) -> int:
        """Attempt count after which a failing claim is dead-lettered (Phase 5)."""
        return settings.WORKER_DEAD_LETTER_THRESHOLD

    # --- Destination collection names (Section 10) -------------------------
    # Defined here so worker modules import them from one place. These are
    # names, not behavior — no DRY conflict with ``config.py``.

    PROJECTIONS_COLLECTION = "ai_invoice_analytics"
    WORKER_STATE_COLLECTION = "ai_analytics_worker_state"
    DEAD_LETTERS_COLLECTION = "ai_analytics_worker_dead_letters"
    WORKER_RUNS_COLLECTION = "ai_analytics_worker_runs"

    # --- Worker identity ---------------------------------------------------

    WORKER_NAME = "ai_analytics_worker"

    # --- Cancellation deadline (Section 1.1.4) -----------------------------
    # The change-stream task must respond to cancellation within this many
    # seconds. Enforced by the lifespan shutdown handler (Phase 7+).
    CANCELLATION_TIMEOUT_SECONDS = 5.0


# Single instance — imported by worker modules as
# ``from ai_analytics_worker.config import worker_config``.
worker_config = WorkerConfig()
