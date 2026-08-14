"""Unit tests for ai_analytics_worker.metrics (in-memory worker counters).

Feature under test: the WorkerMetrics class — counter increment, get, snapshot,
and reset behavior, plus the fail-loud-on-unknown-counter-name contract.

Failure prevented: a typo in a counter name that silently creates a dead
counter would cause metrics to be silently lost, giving operators an
inaccurate picture of worker throughput and error rates.

Test level: unit.
"""

import pytest

from ai_analytics_worker.metrics import WorkerMetrics, worker_metrics


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level worker_metrics singleton before each test."""
    worker_metrics.reset()
    yield


class TestIncrementAndGet:
    """Tests that increment and get work for known counter names."""

    def test_increment_defaults_to_one(self):
        m = WorkerMetrics()
        m.increment("events_received")
        assert m.get("events_received") == 1

    def test_increment_with_custom_amount(self):
        m = WorkerMetrics()
        m.increment("claims_refreshed", 5)
        assert m.get("claims_refreshed") == 5

    def test_increment_accumulates(self):
        m = WorkerMetrics()
        m.increment("projections_created")
        m.increment("projections_created")
        m.increment("projections_created", 3)
        assert m.get("projections_created") == 5

    def test_all_known_counters_start_at_zero(self):
        m = WorkerMetrics()
        snap = m.snapshot()
        for name, value in snap.items():
            assert value == 0, f"Counter {name!r} should start at 0"


class TestUnknownCounterNames:
    """Tests that unknown counter names fail loudly (no silent dead counters)."""

    def test_increment_rejects_unknown_name(self):
        m = WorkerMetrics()
        with pytest.raises(KeyError, match="Unknown worker metric"):
            m.increment("nonexistent_counter")

    def test_get_rejects_unknown_name(self):
        m = WorkerMetrics()
        with pytest.raises(KeyError, match="Unknown worker metric"):
            m.get("nonexistent_counter")

    def test_error_message_lists_known_metrics(self):
        """The error message should help the developer find the right name."""
        m = WorkerMetrics()
        try:
            m.increment("events")
        except KeyError as exc:
            error_text = str(exc)
            # The message should mention at least one known counter to guide
            # the developer toward the correct name.
            assert "events_received" in error_text


class TestSnapshot:
    """Tests that snapshot returns a usable copy of all counters."""

    def test_snapshot_returns_copy_not_reference(self):
        """Mutating the returned dict must not affect internal state."""
        m = WorkerMetrics()
        m.increment("events_received", 3)
        snap = m.snapshot()
        snap["events_received"] = 999
        assert m.get("events_received") == 3

    def test_snapshot_reflects_current_values(self):
        m = WorkerMetrics()
        m.increment("events_received", 10)
        m.increment("claims_refreshed", 7)
        m.increment("dead_letters_created", 2)

        snap = m.snapshot()
        assert snap["events_received"] == 10
        assert snap["claims_refreshed"] == 7
        assert snap["dead_letters_created"] == 2

    def test_snapshot_contains_all_expected_counters(self):
        """All counters defined for the worker pipeline must be present."""
        m = WorkerMetrics()
        snap = m.snapshot()

        expected_counters = {
            "events_received",
            "events_skipped_no_claim_id",
            "claims_refreshed",
            "projections_created",
            "projections_updated",
            "reconciliation_runs",
            "reconciliation_claims_found",
            "backfill_runs",
            "backfill_claims_processed",
            "claim_refresh_errors",
            "claim_refresh_retries",
            "dead_letters_created",
            "resume_tokens_saved",
        }
        assert set(snap.keys()) == expected_counters


class TestReset:
    """Tests that reset zeroes all counters."""

    def test_reset_zeroes_all_counters(self):
        m = WorkerMetrics()
        m.increment("events_received", 10)
        m.increment("claims_refreshed", 5)
        m.increment("dead_letters_created", 3)

        m.reset()

        snap = m.snapshot()
        for name, value in snap.items():
            assert value == 0

    def test_increment_works_after_reset(self):
        m = WorkerMetrics()
        m.increment("events_received", 100)
        m.reset()
        m.increment("events_received")
        assert m.get("events_received") == 1


class TestClampAtZero:
    """Tests that a stray negative amount cannot push a counter below zero."""

    def test_negative_increment_clamps_at_zero(self):
        m = WorkerMetrics()
        m.increment("events_received", 3)
        m.increment("events_received", -10)
        assert m.get("events_received") == 0

    def test_negative_increment_on_zero_stays_zero(self):
        m = WorkerMetrics()
        m.increment("events_received", -5)
        assert m.get("events_received") == 0


class TestSingletonInstance:
    """Tests that the module-level worker_metrics singleton is usable."""

    def test_singleton_is_worker_metrics_instance(self):
        assert isinstance(worker_metrics, WorkerMetrics)

    def test_singleton_can_increment_and_snapshot(self):
        worker_metrics.increment("events_received", 2)
        snap = worker_metrics.snapshot()
        assert snap["events_received"] == 2
