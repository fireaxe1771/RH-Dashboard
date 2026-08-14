"""Tests for ai_analytics_worker.sync_status (Phase 11).

Feature under test: the sync status aggregation that derives a single
status (synced/syncing/catching-up/divergence-detected/error/stopped)
from worker health + sync integrity state + metrics.

Failure prevented:
- The dashboard showing stale data without any indication the sync
  mechanism is broken. The derived status tells the user whether the
  cache is in sync with MongoDB.

Test level: unit (pure derivation from in-memory singletons).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_analytics_worker.sync_status import (
    SYNC_STATUS_SYNCED,
    SYNC_STATUS_SYNCING,
    SYNC_STATUS_CATCHING_UP,
    SYNC_STATUS_DIVERGENCE_DETECTED,
    SYNC_STATUS_ERROR,
    SYNC_STATUS_STOPPED,
    derive_sync_status,
    sync_health_snapshot,
)
from ai_analytics_worker.health import (
    WorkerHealth,
    STATUS_RUNNING,
    STATUS_RECONCILING,
    STATUS_ERROR,
    STATUS_STARTING,
    STATUS_STOPPED,
    worker_health,
)
from ai_analytics_worker.sync_integrity import (
    SyncIntegrityResult,
    SyncIntegrityState,
    sync_integrity_state,
)
from ai_analytics_worker.metrics import worker_metrics
from config import settings


UTC = timezone.utc
NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Reset singletons before each test."""
    worker_health.reset()
    sync_integrity_state.reset()
    worker_metrics.reset()
    yield
    worker_health.reset()
    sync_integrity_state.reset()
    worker_metrics.reset()


class TestDeriveSyncStatus:
    def test_stopped_when_worker_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", False)
        assert derive_sync_status() == SYNC_STATUS_STOPPED

    def test_error_when_worker_in_error_state(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_ERROR)
        worker_health.record_error("something broke")
        assert derive_sync_status() == SYNC_STATUS_ERROR

    def test_error_when_integrity_check_failed(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RUNNING)
        sync_integrity_state.record_error("integrity check failed")
        assert derive_sync_status() == SYNC_STATUS_ERROR

    def test_syncing_when_integrity_check_in_progress(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RUNNING)
        sync_integrity_state.mark_check_started()
        assert derive_sync_status() == SYNC_STATUS_SYNCING

    def test_catching_up_when_divergent_claims_found(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RUNNING)
        result = SyncIntegrityResult(
            divergent_claims=[1, 2, 3],
            completed_at=NOW,
        )
        sync_integrity_state.update_from_result(result)
        assert derive_sync_status() == SYNC_STATUS_CATCHING_UP

    def test_divergence_detected_when_count_mismatch_no_samples(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RUNNING)
        result = SyncIntegrityResult(
            source_count=100,
            projection_count=90,
            count_mismatch=True,
            divergent_claims=[],
            completed_at=NOW,
        )
        sync_integrity_state.update_from_result(result)
        assert derive_sync_status() == SYNC_STATUS_DIVERGENCE_DETECTED

    def test_synced_when_running_and_no_divergence(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RUNNING)
        result = SyncIntegrityResult(
            source_count=100,
            projection_count=100,
            count_mismatch=False,
            divergent_claims=[],
            completed_at=NOW,
        )
        sync_integrity_state.update_from_result(result)
        assert derive_sync_status() == SYNC_STATUS_SYNCED

    def test_syncing_when_reconciling(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RECONCILING)
        assert derive_sync_status() == SYNC_STATUS_SYNCING

    def test_syncing_when_starting(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_STARTING)
        assert derive_sync_status() == SYNC_STATUS_SYNCING

    def test_stopped_when_enabled_but_not_started(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_STOPPED)
        assert derive_sync_status() == SYNC_STATUS_STOPPED


class TestSyncHealthSnapshot:
    def test_snapshot_includes_status_and_integrity(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_RUNNING)
        result = SyncIntegrityResult(
            source_count=100,
            projection_count=100,
            completed_at=NOW,
        )
        sync_integrity_state.update_from_result(result)

        snap = sync_health_snapshot()

        assert snap["status"] == SYNC_STATUS_SYNCED
        assert snap["worker_enabled"] is True
        assert snap["worker_status"] == STATUS_RUNNING
        assert snap["sync_integrity"]["source_count"] == 100
        assert snap["sync_integrity"]["projection_count"] == 100
        assert "metrics" in snap
        assert snap["metrics"]["sync_integrity_checks"] == 0

    def test_snapshot_includes_last_error(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_ERROR)
        worker_health.record_error("worker crashed")
        snap = sync_health_snapshot()
        assert snap["status"] == SYNC_STATUS_ERROR
        assert snap["last_error"] == "worker crashed"

    def test_snapshot_prefers_health_error_over_integrity(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_ANALYTICS_WORKER_ENABLED", True)
        worker_health.set_status(STATUS_ERROR)
        worker_health.record_error("health error")
        sync_integrity_state.record_error("integrity error")
        snap = sync_health_snapshot()
        assert snap["last_error"] == "health error"
