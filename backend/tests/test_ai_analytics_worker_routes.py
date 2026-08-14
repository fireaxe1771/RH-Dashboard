"""Integration tests for ai_analytics_worker.routes (Phase 8).

Feature under test: the worker health/operations HTTP endpoints that expose
the in-memory ``worker_health`` and ``worker_metrics`` singletons to
operators and container probes.

Failure prevented:
- A liveness probe that returns 503 would cause Azure Container Apps to
  restart the container in a loop even when the process is healthy.
  ``/health`` must always return 200 when the process is alive.
- A readiness probe that returns 200 when the worker is in ``STATUS_ERROR``
  would route traffic to a broken worker. ``/ready`` must return 503 in
  that case.
- A status endpoint that leaks internal details without auth would expose
  operational data to unauthenticated callers. ``/status`` must require
  ``get_current_user``.

Test level: integration. Uses the synchronous ``test_client`` fixture from
conftest, which mocks auth so ``get_current_user`` returns standard test
claims. The in-memory ``worker_health`` and ``worker_metrics`` singletons
are reset between tests via the autouse fixture.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ai_analytics_worker.config import WorkerConfig
from ai_analytics_worker.health import (
    worker_health,
    STATUS_ERROR,
    STATUS_RECONCILING,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
)
from ai_analytics_worker.metrics import worker_metrics

AUTH = {"Authorization": "Bearer valid-mock-token"}
HEALTH_URL = "/api/ai-analytics/worker/health"
READY_URL = "/api/ai-analytics/worker/ready"
STATUS_URL = "/api/ai-analytics/worker/status"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_worker_singletons():
    """Reset the in-memory worker_health and worker_metrics before each test.

    These are module-level singletons whose state persists across tests.
    Without a reset, a test that sets STATUS_ERROR would leak into the next
    test and cause false failures.
    """
    worker_health.reset()
    worker_metrics.reset()
    yield
    # Restore after the test so subsequent tests start clean even if the
    # autouse fixture order changes.
    worker_health.reset()
    worker_metrics.reset()


def _patch_worker_enabled(monkeypatch, *, enabled: bool) -> None:
    """Patch the ``worker_config.enabled`` property for a single test.

    ``WorkerConfig.enabled`` reads from the global ``settings`` object,
    which is environment-sourced and not easily mutated per-test. This
    helper replaces the property with a fixed return value so tests can
    exercise both the enabled and disabled code paths without touching env
    vars.
    """
    monkeypatch.setattr(
        WorkerConfig,
        "enabled",
        property(lambda self, v=enabled: v),
    )


# ---------------------------------------------------------------------------
# /health — liveness probe
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for ``/health`` — the liveness probe.

    The liveness probe must always return 200 when the process is alive,
    regardless of worker state. It exists for Azure Container Apps to
    decide whether to restart the container.
    """

    def test_health_returns_200_when_worker_disabled(self, test_client, monkeypatch):
        """``/health`` returns 200 even when the worker is disabled.

        A disabled worker is a configuration choice, not a process failure —
        the container should not be restarted.
        """
        _patch_worker_enabled(monkeypatch, enabled=False)
        response = test_client.get(HEALTH_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"
        assert data["worker_enabled"] is False

    def test_health_returns_200_when_worker_enabled(self, test_client, monkeypatch):
        """``/health`` returns 200 when the worker is enabled."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        response = test_client.get(HEALTH_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"
        assert data["worker_enabled"] is True

    def test_health_does_not_require_auth(self, test_client, monkeypatch):
        """``/health`` is reachable without an Authorization header.

        Container probes don't carry auth tokens, so this endpoint must not
        require authentication.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        response = test_client.get(HEALTH_URL)  # no headers
        assert response.status_code == 200

    def test_health_returns_200_even_when_worker_in_error_state(
        self, test_client, monkeypatch
    ):
        """``/health`` returns 200 even when the worker is in ``STATUS_ERROR``.

        A worker error is not a process death — the liveness probe must stay
        200 so the container isn't restarted while operators investigate.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.record_error("something went wrong")
        assert worker_health.status == STATUS_ERROR

        response = test_client.get(HEALTH_URL)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# /ready — readiness probe
# ---------------------------------------------------------------------------


class TestReadyEndpoint:
    """Tests for ``/ready`` — the readiness probe.

    The readiness probe returns 200 only when the worker is enabled and has
    reached ``STATUS_RUNNING``. It returns 503 in all other cases so Azure
    Container Apps doesn't route traffic to a non-functional worker.
    """

    def test_ready_returns_503_when_worker_disabled(self, test_client, monkeypatch):
        """A disabled worker is not ready to process events."""
        _patch_worker_enabled(monkeypatch, enabled=False)
        response = test_client.get(READY_URL)
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["reason"] == "worker_disabled"

    def test_ready_returns_200_when_worker_running(self, test_client, monkeypatch):
        """An enabled worker in ``STATUS_RUNNING`` is ready."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)

        response = test_client.get(READY_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["status"] == STATUS_RUNNING
        assert data["last_started_at"] is not None

    def test_ready_returns_503_when_worker_stopped_no_start(self, test_client, monkeypatch):
        """A worker that never started (STATUS_STOPPED, no last_started_at) is not ready."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        # worker_health starts in STATUS_STOPPED with no last_started_at
        assert worker_health.status == STATUS_STOPPED
        assert worker_health.last_started_at is None

        response = test_client.get(READY_URL)
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["reason"] == "worker_not_running"

    def test_ready_returns_503_when_worker_in_error(self, test_client, monkeypatch):
        """A worker in ``STATUS_ERROR`` is not ready — operator attention required."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)
        worker_health.record_error("database connection lost")

        response = test_client.get(READY_URL)
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["reason"] == "worker_error"
        assert data["status"] == STATUS_ERROR
        assert data["consecutive_error_count"] == 1

    def test_ready_never_leaks_error_text(self, test_client, monkeypatch):
        """``/ready`` must not include ``last_error`` in any response.

        This endpoint is unauthenticated and the backend Container App has
        external ingress (``external_enabled = true``), so the payload is
        world-readable. ``record_error`` stores raw exception text, and a
        driver-level failure embeds the Atlas cluster hostname, port, and
        timeout configuration. Leaking that to anonymous callers is
        information disclosure. The error text belongs on the auth-protected
        ``/status`` endpoint only.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        secret_ish = (
            "ServerSelectionTimeoutError: "
            "ac-secret-shard-00-00.abc123.mongodb.net:27017: connection refused"
        )
        worker_health.record_error(secret_ish)

        response = test_client.get(READY_URL)
        assert response.status_code == 503
        body = response.json()

        assert "last_error" not in body
        # Belt-and-braces: the hostname must not appear anywhere in the
        # serialized payload, including inside another field.
        assert "mongodb.net" not in response.text
        assert secret_ish not in response.text

    def test_ready_returns_200_when_reconciling(self, test_client, monkeypatch):
        """``STATUS_RECONCILING`` is an active working state, so it is ready.

        A reconciliation scan is normal healthy operation. Returning 503
        during it would make Container Apps pull the instance out of
        rotation on every scan interval.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RECONCILING)

        response = test_client.get(READY_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["status"] == STATUS_RECONCILING

    def test_ready_returns_503_when_starting(self, test_client, monkeypatch):
        """``STATUS_STARTING`` is not ready — the worker hasn't begun processing."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.set_status(STATUS_STARTING)

        response = test_client.get(READY_URL)
        assert response.status_code == 503
        data = response.json()
        assert data["ready"] is False
        assert data["reason"] == "worker_starting"

    def test_ready_does_not_require_auth(self, test_client, monkeypatch):
        """``/ready`` is reachable without an Authorization header.

        Container probes don't carry auth tokens.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)

        response = test_client.get(READY_URL)  # no headers
        assert response.status_code == 200

    def test_ready_includes_last_checkpoint_when_running(self, test_client, monkeypatch):
        """When running, the ready response includes last_checkpoint_at for ops."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)
        worker_health.mark_checkpoint()

        response = test_client.get(READY_URL)
        assert response.status_code == 200
        data = response.json()
        assert data["last_checkpoint_at"] is not None


# ---------------------------------------------------------------------------
# /status — operational dashboard (auth-protected)
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    """Tests for ``/status`` — the operational dashboard endpoint.

    Returns the full ``worker_health.snapshot()`` plus
    ``worker_metrics.snapshot()``. Auth-protected via ``get_current_user``.
    """

    def test_status_returns_full_snapshot_when_authed(self, test_client, monkeypatch):
        """An authenticated request returns the full health + metrics snapshot."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)
        worker_health.mark_checkpoint()
        worker_metrics.increment("events_received", 10)
        worker_metrics.increment("claims_refreshed", 7)

        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        data = response.json()

        assert data["enabled"] is True

        health = data["health"]
        assert health["_id"] == "ai_analytics_worker"
        assert health["status"] == STATUS_RUNNING
        assert health["last_started_at"] is not None
        assert health["last_checkpoint_at"] is not None
        assert health["consecutive_error_count"] == 0

        metrics = data["metrics"]
        assert metrics["events_received"] == 10
        assert metrics["claims_refreshed"] == 7
        # All known counters must be present
        assert "projections_created" in metrics
        assert "dead_letters_created" in metrics
        assert "reconciliation_runs" in metrics

    def test_status_returns_200_when_worker_disabled(self, test_client, monkeypatch):
        """``/status`` returns 200 with ``enabled=false`` when the worker is disabled.

        The status endpoint is informational — it should report the current
        state, not refuse to answer. Operators may check it to confirm the
        worker is intentionally disabled.
        """
        _patch_worker_enabled(monkeypatch, enabled=False)

        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    def test_status_includes_error_state(self, test_client, monkeypatch):
        """When the worker is in error, ``/status`` reports the error details."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.record_error("fatal crash")
        worker_health.record_error("still crashing")

        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        health = data["health"]
        assert health["status"] == STATUS_ERROR
        assert health["last_error"] == "still crashing"
        assert health["consecutive_error_count"] == 2

    def test_status_is_the_only_endpoint_exposing_error_text(
        self, test_client, monkeypatch
    ):
        """``last_error`` is exposed on the authed ``/status``, not on ``/ready``.

        Pins the split: operators who authenticate get the diagnostic detail,
        anonymous probe traffic does not. If someone later adds
        ``last_error`` back to ``/ready``, this test and
        ``test_ready_never_leaks_error_text`` both fail.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.record_error("connection refused to internal-host:27017")

        authed = test_client.get(STATUS_URL, headers=AUTH)
        assert authed.status_code == 200
        assert (
            authed.json()["health"]["last_error"]
            == "connection refused to internal-host:27017"
        )

        anon = test_client.get(READY_URL)
        assert "internal-host" not in anon.text

    def test_status_metrics_counters_are_cumulative(self, test_client, monkeypatch):
        """Metrics counters accumulate across increments (cumulative since process start)."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_metrics.increment("events_received", 5)
        worker_metrics.increment("events_received", 3)
        worker_metrics.increment("dead_letters_created", 2)

        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        metrics = response.json()["metrics"]
        assert metrics["events_received"] == 8
        assert metrics["dead_letters_created"] == 2

    def test_status_reflects_worker_version_and_schema_version(
        self, test_client, monkeypatch
    ):
        """The status response includes worker_version and projection_schema_version."""
        _patch_worker_enabled(monkeypatch, enabled=True)

        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        health = response.json()["health"]
        assert "worker_version" in health
        assert isinstance(health["worker_version"], str)
        assert "projection_schema_version" in health
        assert isinstance(health["projection_schema_version"], int)

    def test_status_response_is_serializable(self, test_client, monkeypatch):
        """All datetime fields in the snapshot must serialize to ISO 8601 strings.

        FastAPI serializes timezone-aware datetimes automatically, but a
        naive datetime would produce a 500. This test verifies the snapshot
        is JSON-serializable end-to-end through the FastAPI response cycle.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        # Set all timestamps to timezone-aware UTC values.
        worker_health.mark_started()
        worker_health.mark_completed()
        worker_health.mark_successful_event()
        worker_health.mark_checkpoint()

        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200
        health = response.json()["health"]
        # All timestamp fields must be ISO 8601 strings (not None, not raw
        # datetime objects that would fail JSON serialization).
        for field in (
            "last_started_at",
            "last_completed_at",
            "last_successful_event_at",
            "last_checkpoint_at",
        ):
            value = health[field]
            assert value is not None
            assert isinstance(value, str)
            # Must be parseable as a datetime
            parsed = datetime.fromisoformat(value)
            assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


class TestStatusAuthEnforcement:
    """Tests that ``/status`` requires authentication while probes do not."""

    def test_status_returns_401_without_auth(self, test_client, monkeypatch):
        """``/status`` requires an Authorization header — 401 without it.

        ``get_current_user`` uses ``HTTPBearer(auto_error=False)`` and raises
        an explicit 401 when credentials are absent, so the status code is
        deterministic — assert on it exactly rather than accepting 401 or
        403, which would let a future auth regression slip through.
        """
        _patch_worker_enabled(monkeypatch, enabled=True)
        response = test_client.get(STATUS_URL)  # no headers
        assert response.status_code == 401

    def test_status_returns_200_with_valid_auth(self, test_client, monkeypatch):
        """``/status`` returns 200 with a valid (mocked) auth token."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        response = test_client.get(STATUS_URL, headers=AUTH)
        assert response.status_code == 200

    def test_health_and_ready_are_unauthenticated(self, test_client, monkeypatch):
        """Both probes are reachable without auth — they're for container probes."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)

        # No headers on either
        assert test_client.get(HEALTH_URL).status_code == 200
        assert test_client.get(READY_URL).status_code == 200


# ---------------------------------------------------------------------------
# Snapshot independence — /status does not mutate worker state
# ---------------------------------------------------------------------------


class TestSnapshotIndependence:
    """Tests that calling ``/status`` does not mutate the in-memory singletons."""

    def test_status_does_not_reset_metrics(self, test_client, monkeypatch):
        """Reading ``/status`` must not reset the metrics counters."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_metrics.increment("events_received", 42)

        test_client.get(STATUS_URL, headers=AUTH)

        # The counter must still be 42 — reading the status endpoint is
        # side-effect-free.
        assert worker_metrics.get("events_received") == 42

    def test_status_does_not_change_health_state(self, test_client, monkeypatch):
        """Reading ``/status`` must not change the worker health status."""
        _patch_worker_enabled(monkeypatch, enabled=True)
        worker_health.mark_started()
        worker_health.set_status(STATUS_RUNNING)

        test_client.get(STATUS_URL, headers=AUTH)

        assert worker_health.status == STATUS_RUNNING
        assert worker_health.last_started_at is not None
