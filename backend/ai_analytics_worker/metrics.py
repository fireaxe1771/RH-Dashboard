"""AI Analytics Worker — in-memory metrics counters.

Counts the events the worker processes, the projections it builds, the errors
it encounters, and the dead-letters it produces. Counters are in-memory only
in Phase 1; Phase 8 (Health and operations) will expose them via a health
endpoint.

Source: none (in-memory only).
Destination: none directly — Phase 8 will expose ``snapshot()`` via the
worker health endpoint.
Architectural constraints: no I/O. All counters are non-negative integers.
"""

from __future__ import annotations

from typing import Dict


class WorkerMetrics:
    """In-memory worker metrics counters.

    Responsibility: track cumulative counts of worker activity so operators
    can observe throughput and error rates without querying the destination
    collections.

    Lifecycle: a single instance is created at module import as
    ``worker_metrics`` and lives for the lifetime of the process. Counters are
    not reset between cycles — they are cumulative since process start.

    Inputs: increment calls from worker modules.
    Outputs: ``snapshot()`` returns a flat dict of counter name → count.
    Dependencies: none.
    Error behavior: counters never raise; ``increment`` clamps at zero so a
    stray negative decrement cannot corrupt a counter.
    """

    def __init__(self) -> None:
        self._counters: Dict[str, int] = {
            "events_received": 0,
            "events_skipped_no_claim_id": 0,
            "claims_refreshed": 0,
            "projections_created": 0,
            "projections_updated": 0,
            "reconciliation_runs": 0,
            "reconciliation_claims_found": 0,
            "backfill_runs": 0,
            "backfill_claims_processed": 0,
            "claim_refresh_errors": 0,
            "claim_refresh_retries": 0,
            "dead_letters_created": 0,
            "resume_tokens_saved": 0,
        }

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a named counter by ``amount`` (default 1).

        Raises:
            KeyError: if ``name`` is not a known counter. This is intentional
                — typos in counter names should fail loudly rather than
                silently create dead counters.
        """
        if name not in self._counters:
            raise KeyError(
                f"Unknown worker metric: {name!r}. "
                f"Known metrics: {sorted(self._counters)}"
            )
        # Clamp at zero — a stray negative amount cannot go below zero.
        new_value = self._counters[name] + amount
        self._counters[name] = max(0, new_value)

    def get(self, name: str) -> int:
        """Return the current value of a named counter.

        Raises:
            KeyError: if ``name`` is not a known counter.
        """
        if name not in self._counters:
            raise KeyError(
                f"Unknown worker metric: {name!r}. "
                f"Known metrics: {sorted(self._counters)}"
            )
        return self._counters[name]

    def snapshot(self) -> Dict[str, int]:
        """Return a copy of all counters as a flat dict."""
        return dict(self._counters)

    def reset(self) -> None:
        """Reset all counters to zero.

        Used by tests to start each test from a clean slate. Not intended for
        production use (counters are cumulative since process start).
        """
        for name in self._counters:
            self._counters[name] = 0


# Single instance — imported by worker modules as
# ``from ai_analytics_worker.metrics import worker_metrics``.
worker_metrics = WorkerMetrics()
