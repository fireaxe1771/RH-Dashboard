"""Unit tests for ai_analytics_worker.health (in-memory worker health state).

Feature under test: the WorkerHealth class — status transitions, lifecycle
timestamp tracking, error counting, and snapshot serialization that mirrors
the ai_analytics_worker_state document (Phase 0 plan Section 10.2).

Failure prevented: a health state that silently accepts invalid status values
or loses error counts would give operators a false picture of worker state and
prevent detection of a stuck or failing worker.

Test level: unit.
"""

from datetime import UTC, datetime, timedelta

import pytest

from ai_analytics_worker.config import worker_config
from ai_analytics_worker.health import (
    WorkerHealth,
    worker_health,
    STATUS_STOPPED,
    STATUS_STARTING,
    STATUS_RUNNING,
    STATUS_RECONCILING,
    STATUS_ERROR,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level worker_health singleton before each test."""
    worker_health.reset()
    yield


class TestStatusTransitions:
    """Tests that set_status accepts valid statuses and rejects invalid ones."""

    def test_set_status_accepts_all_known_statuses(self):
        h = WorkerHealth()
        for status in (STATUS_STOPPED, STATUS_STARTING, STATUS_RUNNING,
                       STATUS_RECONCILING, STATUS_ERROR):
            h.set_status(status)
            assert h.status == status

    def test_set_status_rejects_unknown_status(self):
        h = WorkerHealth()
        with pytest.raises(ValueError, match="Unknown worker status"):
            h.set_status("paused")

    def test_set_status_rejects_empty_string(self):
        h = WorkerHealth()
        with pytest.raises(ValueError, match="Unknown worker status"):
            h.set_status("")

    def test_default_status_is_stopped(self):
        h = WorkerHealth()
        assert h.status == STATUS_STOPPED


class TestLifecycleTimestamps:
    """Tests that lifecycle marks set the correct timestamps."""

    def test_mark_started_sets_last_started_at_and_clears_errors(self):
        h = WorkerHealth()
        h.record_error("previous error")
        assert h.consecutive_error_count == 1
        assert h.last_error == "previous error"

        h.mark_started()

        assert h.last_started_at is not None
        assert h.consecutive_error_count == 0
        assert h.last_error is None
        # Timestamp should be recent (within the last second)
        now = datetime.now(UTC)
        assert (now - h.last_started_at) < timedelta(seconds=1)

    def test_mark_completed_sets_last_completed_at(self):
        h = WorkerHealth()
        assert h.last_completed_at is None
        h.mark_completed()
        assert h.last_completed_at is not None

    def test_mark_successful_event_sets_last_successful_event_at(self):
        h = WorkerHealth()
        assert h.last_successful_event_at is None
        h.mark_successful_event()
        assert h.last_successful_event_at is not None

    def test_mark_checkpoint_sets_last_checkpoint_at(self):
        h = WorkerHealth()
        assert h.last_checkpoint_at is None
        h.mark_checkpoint()
        assert h.last_checkpoint_at is not None

    def test_timestamps_are_timezone_aware_utc(self):
        """All timestamps must be timezone-aware UTC for correct BSON storage."""
        h = WorkerHealth()
        h.mark_started()
        h.mark_completed()
        h.mark_successful_event()
        h.mark_checkpoint()

        for ts in (h.last_started_at, h.last_completed_at,
                   h.last_successful_event_at, h.last_checkpoint_at):
            assert ts is not None
            assert ts.tzinfo is not None
            assert ts.utcoffset() == timedelta(0)


class TestErrorTracking:
    """Tests that error recording and clearing work correctly."""

    def test_record_error_sets_error_and_increments_count(self):
        h = WorkerHealth()
        assert h.consecutive_error_count == 0
        assert h.last_error is None
        assert h.status == STATUS_STOPPED

        h.record_error("database connection lost")

        assert h.last_error == "database connection lost"
        assert h.consecutive_error_count == 1
        assert h.status == STATUS_ERROR

    def test_record_error_accumulates_consecutive_count(self):
        h = WorkerHealth()
        h.record_error("error 1")
        h.record_error("error 2")
        h.record_error("error 3")

        assert h.consecutive_error_count == 3
        assert h.last_error == "error 3"

    def test_clear_error_resets_error_state(self):
        h = WorkerHealth()
        h.record_error("some error")
        assert h.consecutive_error_count == 1

        h.clear_error()

        assert h.last_error is None
        assert h.consecutive_error_count == 0


class TestSnapshot:
    """Tests that snapshot() produces the ai_analytics_worker_state document shape."""

    def test_snapshot_returns_all_section_10_2_fields(self):
        """snapshot() must include every field defined in Section 10.2."""
        h = WorkerHealth()
        snap = h.snapshot()

        expected_keys = {
            "_id",
            "worker_version",
            "projection_schema_version",
            "last_started_at",
            "last_completed_at",
            "last_successful_event_at",
            "last_checkpoint_at",
            "status",
            "last_error",
            "consecutive_error_count",
        }
        assert set(snap.keys()) == expected_keys

    def test_snapshot_id_is_worker_name(self):
        h = WorkerHealth()
        snap = h.snapshot()
        assert snap["_id"] == worker_config.WORKER_NAME

    def test_snapshot_reflects_current_state(self):
        h = WorkerHealth()
        h.mark_started()
        h.set_status(STATUS_RUNNING)
        h.record_error("test error")
        h.mark_checkpoint()

        snap = h.snapshot()

        assert snap["status"] == STATUS_ERROR  # record_error sets ERROR
        assert snap["last_error"] == "test error"
        assert snap["consecutive_error_count"] == 1
        assert snap["last_started_at"] == h.last_started_at
        assert snap["last_checkpoint_at"] == h.last_checkpoint_at
        assert snap["worker_version"] == worker_config.worker_version
        assert snap["projection_schema_version"] == worker_config.projection_schema_version

    def test_snapshot_returns_independent_copy(self):
        """Mutating the returned dict must not affect the health state."""
        h = WorkerHealth()
        snap = h.snapshot()
        snap["status"] = "tampered"
        snap["consecutive_error_count"] = 999

        assert h.status != "tampered"
        assert h.consecutive_error_count != 999


class TestSingletonInstance:
    """Tests that the module-level worker_health singleton is usable."""

    def test_singleton_is_worker_health_instance(self):
        assert isinstance(worker_health, WorkerHealth)

    def test_singleton_can_transition_states(self):
        worker_health.set_status(STATUS_RUNNING)
        assert worker_health.status == STATUS_RUNNING
        worker_health.set_status(STATUS_STOPPED)
        assert worker_health.status == STATUS_STOPPED
