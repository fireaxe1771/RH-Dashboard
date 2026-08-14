"""Unit tests for ai_analytics_worker.source_repository (Phase 2).

Feature under test: the source repository wrapper that adds timeout
enforcement, retry with exponential backoff for transient errors, and
structured logging over the existing mongo_repository read functions.

Failure prevented: a stuck Mongo query that hangs indefinitely would starve
the FastAPI event loop and block all request handling. A transient network
blip that causes a single query failure would unnecessarily dead-letter a
claim if not retried.

Test level: unit.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    ExecutionTimeout,
    NetworkTimeout,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from ai_analytics_worker.config import WorkerConfig
from ai_analytics_worker.source_repository import (
    _backoff_delay,
    _is_transient,
    get_ai_line_items_for_claim_with_retry,
    get_agent_conversations_for_claim_with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def patch_worker_config(monkeypatch, **overrides):
    """Patch worker_config properties with test-specific values.

    Properties on WorkerConfig read from the global ``settings`` object, which
    is environment-sourced and not easily mutated per-test. This helper
    replaces specific properties on the class with fixed return values so
    tests can control timeout and retry behavior without touching env vars.
    """
    for name, value in overrides.items():
        monkeypatch.setattr(
            WorkerConfig,
            name,
            property(lambda self, v=value: v),
        )


# ---------------------------------------------------------------------------
# Transient error classification
# ---------------------------------------------------------------------------


class TestIsTransient:
    """Tests that _is_transient correctly classifies PyMongo and asyncio errors."""

    def test_server_selection_timeout_is_transient(self):
        assert _is_transient(ServerSelectionTimeoutError("no servers"))

    def test_network_timeout_is_transient(self):
        assert _is_transient(NetworkTimeout("timed out"))

    def test_autoreconnect_is_transient(self):
        assert _is_transient(AutoReconnect("primary stepped down"))

    def test_connection_failure_is_transient(self):
        assert _is_transient(ConnectionFailure("connection dropped"))

    def test_execution_timeout_is_transient(self):
        assert _is_transient(ExecutionTimeout("query exceeded time limit"))

    def test_asyncio_timeout_is_transient(self):
        assert _is_transient(asyncio.TimeoutError())

    def test_operation_failure_is_not_transient(self):
        assert not _is_transient(OperationFailure("bad query"))

    def test_value_error_is_not_transient(self):
        assert not _is_transient(ValueError("bad input"))

    def test_type_error_is_not_transient(self):
        assert not _is_transient(TypeError("wrong type"))

    def test_generic_exception_is_not_transient(self):
        assert not _is_transient(Exception("unknown"))


# ---------------------------------------------------------------------------
# Backoff delay
# ---------------------------------------------------------------------------


class TestBackoffDelay:
    """Tests that _backoff_delay produces exponential delays with a cap."""

    def test_first_attempt_uses_base_delay(self):
        assert _backoff_delay(1) == 0.1

    def test_second_attempt_doubles(self):
        assert _backoff_delay(2) == 0.2

    def test_third_attempt_doubles_again(self):
        assert _backoff_delay(3) == 0.4

    def test_delay_is_capped_at_max(self):
        # 2^10 * 0.1 = 102.4, should be capped at 2.0
        assert _backoff_delay(11) == 2.0

    def test_delay_never_exceeds_max(self):
        for attempt in range(1, 20):
            assert _backoff_delay(attempt) <= 2.0


# ---------------------------------------------------------------------------
# Delegation (wrapper calls the underlying function)
# ---------------------------------------------------------------------------


class TestDelegation:
    """Tests that the wrapper functions delegate to the underlying mongo_repository functions."""

    @pytest.mark.asyncio
    async def test_get_ai_line_items_delegates_to_underlying_function(self):
        ai_db = object()  # sentinel — the mock doesn't need it
        expected = {"_id": "abc", "claim_id": 12345}
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_fn:
            result = await get_ai_line_items_for_claim_with_retry(ai_db, 12345)

        assert result == expected
        mock_fn.assert_awaited_once_with(ai_db, 12345)

    @pytest.mark.asyncio
    async def test_get_agent_conversations_delegates_to_underlying_function(self):
        ai_db = object()
        expected = [{"_id": "conv1", "agent": "workflow"}]
        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_fn:
            result = await get_agent_conversations_for_claim_with_retry(ai_db, 12345)

        assert result == expected
        mock_fn.assert_awaited_once_with(ai_db, 12345)

    @pytest.mark.asyncio
    async def test_get_ai_line_items_returns_none_when_not_found(self):
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await get_ai_line_items_for_claim_with_retry(object(), 99999)

        assert result is None


# ---------------------------------------------------------------------------
# Successful retry (transient error then success)
# ---------------------------------------------------------------------------


class TestTransientRetrySucceeds:
    """Tests that a transient error on early attempts is retried and succeeds."""

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt_after_transient_error(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        expected = {"_id": "abc", "claim_id": 12345}
        # AsyncMock with side_effect as a list: first await raises, second returns.
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=[ServerSelectionTimeoutError("no servers"), expected],
        ):
            result = await get_ai_line_items_for_claim_with_retry(object(), 12345)

        assert result == expected

    @pytest.mark.asyncio
    async def test_succeeds_on_third_attempt_after_two_transient_errors(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        expected = [{"_id": "conv1"}]
        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new_callable=AsyncMock,
            side_effect=[
                AutoReconnect("primary stepped down"),
                AutoReconnect("still electing"),
                expected,
            ],
        ):
            result = await get_agent_conversations_for_claim_with_retry(object(), 12345)

        assert result == expected

    @pytest.mark.asyncio
    async def test_recovery_logs_success_after_retry(self, monkeypatch, caplog):
        """When a retry succeeds, an INFO log records which attempt succeeded."""
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        caplog.set_level(logging.INFO, logger="ai_analytics_worker.source_repository")
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=[NetworkTimeout("slow"), {"claim_id": 12345}],
        ):
            await get_ai_line_items_for_claim_with_retry(object(), 12345)

        success_logs = [
            r for r in caplog.records if "succeeded on attempt" in r.message
        ]
        assert len(success_logs) == 1
        assert "claim_id=12345" in success_logs[0].message


# ---------------------------------------------------------------------------
# Non-transient error raises immediately (no retry)
# ---------------------------------------------------------------------------


class TestNonTransientErrorRaisesImmediately:
    """Tests that a non-transient error is raised without retrying."""

    @pytest.mark.asyncio
    async def test_operation_failure_raises_immediately(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        mock_fn = AsyncMock(side_effect=OperationFailure("bad query syntax"))
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new=mock_fn,
        ):
            with pytest.raises(OperationFailure, match="bad query syntax"):
                await get_ai_line_items_for_claim_with_retry(object(), 12345)

        # Must NOT have retried — only one call.
        assert mock_fn.await_count == 1

    @pytest.mark.asyncio
    async def test_value_error_raises_immediately(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        mock_fn = AsyncMock(side_effect=ValueError("claim_id is not valid"))
        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new=mock_fn,
        ):
            with pytest.raises(ValueError, match="claim_id is not valid"):
                await get_agent_conversations_for_claim_with_retry(object(), 12345)

        assert mock_fn.await_count == 1

    @pytest.mark.asyncio
    async def test_non_transient_error_logs_error_with_claim_id(self, monkeypatch, caplog):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        caplog.set_level(logging.ERROR, logger="ai_analytics_worker.source_repository")
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=OperationFailure("bad query"),
        ):
            with pytest.raises(OperationFailure):
                await get_ai_line_items_for_claim_with_retry(object(), 99999)

        error_logs = [
            r for r in caplog.records if "non-transient error" in r.message
        ]
        assert len(error_logs) == 1
        assert "claim_id=99999" in error_logs[0].message
        assert "OperationFailure" in error_logs[0].message


# ---------------------------------------------------------------------------
# All attempts fail with transient errors
# ---------------------------------------------------------------------------


class TestAllAttemptsExhausted:
    """Tests that exhausted retries raise the last transient exception."""

    @pytest.mark.asyncio
    async def test_raises_last_error_after_all_retries(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        mock_fn = AsyncMock(side_effect=ConnectionFailure("connection dropped"))
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new=mock_fn,
        ):
            with pytest.raises(ConnectionFailure, match="connection dropped"):
                await get_ai_line_items_for_claim_with_retry(object(), 12345)

        # 3 retries = 3 total attempts.
        assert mock_fn.await_count == 3

    @pytest.mark.asyncio
    async def test_respects_max_retries_setting(self, monkeypatch):
        """If max_retries=2, only 2 total attempts are made."""
        patch_worker_config(monkeypatch, max_retries=2, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        mock_fn = AsyncMock(side_effect=NetworkTimeout("timed out"))
        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new=mock_fn,
        ):
            with pytest.raises(NetworkTimeout):
                await get_agent_conversations_for_claim_with_retry(object(), 12345)

        assert mock_fn.await_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_log_final_failure(self, monkeypatch, caplog):
        patch_worker_config(monkeypatch, max_retries=2, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        caplog.set_level(logging.ERROR, logger="ai_analytics_worker.source_repository")
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=ServerSelectionTimeoutError("no servers"),
        ):
            with pytest.raises(ServerSelectionTimeoutError):
                await get_ai_line_items_for_claim_with_retry(object(), 12345)

        final_logs = [
            r for r in caplog.records if "failed after" in r.message
        ]
        assert len(final_logs) == 1
        assert "claim_id=12345" in final_logs[0].message
        assert "2/2" in final_logs[0].message


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


class TestTimeoutEnforcement:
    """Tests that a slow query is cancelled by the timeout and retried."""

    @pytest.mark.asyncio
    async def test_timeout_fires_and_retry_succeeds(self, monkeypatch):
        """A query that exceeds the timeout is cancelled; the retry succeeds."""
        # 50ms timeout — fast enough for a unit test.
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=50)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        state = {"calls": 0}

        # Replace with a real async function so the coroutine is properly
        # created and awaited by asyncio.wait_for.
        async def mock_get(ai_db, claim_id):
            state["calls"] += 1
            if state["calls"] == 1:
                # Sleep longer than the timeout — will be cancelled.
                await asyncio.sleep(0.5)
            return {"claim_id": 12345}

        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new=mock_get,
        ):
            result = await get_ai_line_items_for_claim_with_retry(object(), 12345)

        assert result == {"claim_id": 12345}
        assert state["calls"] == 2  # first timed out, second succeeded

    @pytest.mark.asyncio
    async def test_timeout_all_attempts_exhausted(self, monkeypatch):
        """A query that always times out exhausts retries and raises TimeoutError."""
        patch_worker_config(monkeypatch, max_retries=2, source_query_timeout_ms=50)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        call_count = {"n": 0}

        async def slow_always(ai_db, claim_id):
            call_count["n"] += 1
            await asyncio.sleep(0.5)  # always exceeds timeout

        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new=slow_always,
        ):
            with pytest.raises(asyncio.TimeoutError):
                await get_agent_conversations_for_claim_with_retry(object(), 12345)

        assert call_count["n"] == 2  # both attempts timed out


# ---------------------------------------------------------------------------
# Cancellation propagation
# ---------------------------------------------------------------------------


class TestCancellationPropagation:
    """Tests that external CancelledError propagates without retrying."""

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_immediately(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        mock_fn = AsyncMock(side_effect=asyncio.CancelledError())
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new=mock_fn,
        ):
            with pytest.raises(asyncio.CancelledError):
                await get_ai_line_items_for_claim_with_retry(object(), 12345)

        # Must NOT retry on CancelledError.
        assert mock_fn.await_count == 1

    @pytest.mark.asyncio
    async def test_cancelled_error_does_not_swallow(self, monkeypatch):
        """The wrapper must not catch CancelledError in its generic Exception handler."""
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ):
            # If CancelledError were swallowed by the except Exception clause,
            # this would either return None or retry. Instead it must propagate.
            with pytest.raises(asyncio.CancelledError):
                await get_agent_conversations_for_claim_with_retry(object(), 12345)


# ---------------------------------------------------------------------------
# Backoff delay applied between retries
# ---------------------------------------------------------------------------


class TestBackoffApplied:
    """Tests that the exponential backoff delay is actually awaited between retries."""

    @pytest.mark.asyncio
    async def test_sleep_is_called_between_retries(self, monkeypatch):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        sleep_calls = []

        async def track_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(
            "ai_analytics_worker.source_repository.asyncio.sleep",
            track_sleep,
        )

        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=[
                ConnectionFailure("dropped"),
                ConnectionFailure("still dropping"),
                {"claim_id": 12345},
            ],
        ):
            await get_ai_line_items_for_claim_with_retry(object(), 12345)

        # 2 failures → 2 backoff sleeps (between attempt 1→2 and 2→3).
        assert len(sleep_calls) == 2
        # First backoff is for attempt 1, second for attempt 2.
        assert sleep_calls[0] == _backoff_delay(1)
        assert sleep_calls[1] == _backoff_delay(2)

    @pytest.mark.asyncio
    async def test_no_sleep_on_first_attempt_success(self, monkeypatch):
        """No backoff sleep when the first attempt succeeds."""
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        sleep_calls = []

        async def track_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(
            "ai_analytics_worker.source_repository.asyncio.sleep",
            track_sleep,
        )

        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            return_value={"claim_id": 12345},
        ):
            await get_ai_line_items_for_claim_with_retry(object(), 12345)

        assert sleep_calls == []

    @pytest.mark.asyncio
    async def test_no_sleep_after_non_transient_error(self, monkeypatch):
        """No backoff sleep when a non-transient error raises immediately."""
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        sleep_calls = []

        async def track_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(
            "ai_analytics_worker.source_repository.asyncio.sleep",
            track_sleep,
        )

        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=OperationFailure("bad query"),
        ):
            with pytest.raises(OperationFailure):
                await get_ai_line_items_for_claim_with_retry(object(), 12345)

        assert sleep_calls == []


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


class TestStructuredLogging:
    """Tests that retry/failure logs include claim_id and worker_version."""

    @pytest.mark.asyncio
    async def test_transient_retry_warning_includes_claim_id(self, monkeypatch, caplog):
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)
        monkeypatch.setattr(
            "ai_analytics_worker.source_repository._backoff_delay",
            lambda attempt: 0.0,
        )

        caplog.set_level(logging.WARNING, logger="ai_analytics_worker.source_repository")
        with patch(
            "ai_analytics_worker.source_repository.get_ai_line_items_for_claim",
            new_callable=AsyncMock,
            side_effect=[ConnectionFailure("dropped"), {"claim_id": 777}],
        ):
            await get_ai_line_items_for_claim_with_retry(object(), 777)

        warning_logs = [
            r for r in caplog.records if "transient error" in r.message
        ]
        assert len(warning_logs) == 1
        assert "claim_id=777" in warning_logs[0].message
        assert "ConnectionFailure" in warning_logs[0].message

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_no_retry_log(self, monkeypatch, caplog):
        """First-attempt success does not produce a retry-succeeded log."""
        patch_worker_config(monkeypatch, max_retries=3, source_query_timeout_ms=5000)

        caplog.set_level(logging.INFO, logger="ai_analytics_worker.source_repository")
        with patch(
            "ai_analytics_worker.source_repository.get_agent_conversations_for_claim",
            new_callable=AsyncMock,
            return_value=[{"_id": "c1"}],
        ):
            await get_agent_conversations_for_claim_with_retry(object(), 12345)

        retry_logs = [
            r for r in caplog.records if "succeeded on attempt" in r.message
        ]
        assert retry_logs == []
