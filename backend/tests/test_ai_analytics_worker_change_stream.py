"""Unit tests for ai_analytics_worker.change_stream_listener (Phase 5).

Feature under test: the change stream listener that watches
``ai_line_items`` and ``ai_agent_conversations`` for relevant changes,
extracts the affected claim_id, calls ``refresh_claim`` to rebuild the
projection, and persists the resume token.

Failure prevented:
- A change stream that can't be cancelled would hang the FastAPI shutdown.
  The stop_event check must allow prompt cancellation.
- A change stream that dies on one bad event would miss all subsequent
  events. Bad events must be dead-lettered (via refresh_claim) and the
  stream continues.
- A change stream that loses the resume token on restart would miss events.
  The token must be persisted after each event.
- A change stream that never yields would starve the event loop.
  The max_claims_per_cycle yield prevents this.

Test level: unit. The change stream is mocked via a fake async iterator
since mongomock doesn't support change streams.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_analytics_worker.change_stream_listener import (
    _extract_claim_id,
    _extract_resume_token,
    _process_change_event,
    run_change_stream_listener,
)
from ai_analytics_worker.config import worker_config


# ---------------------------------------------------------------------------
# Fake change stream for mocking ai_db.watch()
# ---------------------------------------------------------------------------


class FakeChangeStream:
    """A fake async-iterable change stream that mimics Motor's behavior.

    A real Motor change stream blocks on ``__anext__`` waiting for the next
    event from the server. This fake replicates that behavior: when there
    are no more pre-seeded events, it blocks until ``stop_event`` is set
    (then raises ``StopAsyncIteration`` to end the stream).

    This is critical for testing — if the fake returned ``StopAsyncIteration``
    immediately when empty, the listener would spin in a tight loop opening
    new streams.
    """

    def __init__(self, events=None, raise_on_iterate=None, stop_event=None):
        """Initialize with a list of event dicts to yield.

        Args:
            events: list of change event dicts to yield in order.
            raise_on_iterate: exception to raise on the first ``__anext__``
                call (for testing stream-break handling).
            stop_event: when the stream runs out of events, block until this
                event is set, then raise ``StopAsyncIteration``. If None,
                raise ``StopAsyncIteration`` immediately when empty.
        """
        self._events = list(events) if events else []
        self._raise_on_iterate = raise_on_iterate
        self._stop_event = stop_event
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_on_iterate is not None:
            exc = self._raise_on_iterate
            self._raise_on_iterate = None
            raise exc

        if self._events:
            return self._events.pop(0)

        # No more pre-seeded events. If we have a stop_event, block until
        # it's set (mimicking a real change stream waiting for server events).
        if self._stop_event is not None:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.01)
        raise StopAsyncIteration

    async def close(self):
        self._closed = True


class FakeAIDB:
    """A fake ai_db that returns FakeChangeStream instances from ``watch()``."""

    def __init__(self, stream=None, stream_factory=None):
        """Initialize with either a fixed stream or a factory function.

        Args:
            stream: a FakeChangeStream to return from every ``watch()`` call.
            stream_factory: a callable that returns a new FakeChangeStream
                on each ``watch()`` call. Used when different streams are
                needed for restart testing.
        """
        self._stream = stream
        self._stream_factory = stream_factory
        self.watch_calls = []

    def watch(self, **kwargs):
        self.watch_calls.append(kwargs)
        if self._stream_factory is not None:
            return self._stream_factory()
        return self._stream


def make_change_event(
    operation_type: str,
    claim_id=None,
    collection: str = "ai_line_items",
):
    """Build a minimal change stream event dict."""
    event = {
        "_id": {"_data": f"token_{claim_id}_{operation_type}"},
        "operationType": operation_type,
        "ns": {"db": "AI_FEE_CALC_MULTI_AGENT_PROD", "coll": collection},
    }
    if operation_type != "delete":
        event["fullDocument"] = {
            "_id": "doc_1",
            "claim_id": claim_id,
        }
    return event


# ---------------------------------------------------------------------------
# _extract_claim_id
# ---------------------------------------------------------------------------


class TestExtractClaimId:
    """Tests for the claim_id extraction from change events."""

    def test_extracts_integer_claim_id(self):
        event = make_change_event("insert", claim_id=12345)
        assert _extract_claim_id(event) == 12345

    def test_extracts_string_numeric_claim_id(self):
        event = make_change_event("insert", claim_id="12345")
        assert _extract_claim_id(event) == 12345

    def test_returns_none_for_missing_claim_id(self):
        event = make_change_event("insert", claim_id=None)
        assert _extract_claim_id(event) is None

    def test_returns_none_for_non_numeric_claim_id(self):
        event = make_change_event("insert", claim_id="not-a-number")
        assert _extract_claim_id(event) is None

    def test_returns_none_for_delete_event(self):
        """Delete events have no fullDocument, so claim_id can't be extracted."""
        event = make_change_event("delete")
        assert _extract_claim_id(event) is None

    def test_returns_none_for_missing_full_document(self):
        event = {"operationType": "insert", "_id": {"_data": "token"}}
        assert _extract_claim_id(event) is None


# ---------------------------------------------------------------------------
# _extract_resume_token
# ---------------------------------------------------------------------------


class TestExtractResumeToken:
    """Tests for resume token extraction from change events."""

    def test_extracts_token_from_id(self):
        token = {"_data": "abc123"}
        event = {"_id": token, "operationType": "insert"}
        assert _extract_resume_token(event) == token

    def test_returns_none_for_missing_id(self):
        event = {"operationType": "insert"}
        assert _extract_resume_token(event) is None


# ---------------------------------------------------------------------------
# _process_change_event
# ---------------------------------------------------------------------------


class TestProcessEvent:
    """Tests for processing a single change event."""

    @pytest.mark.asyncio
    async def test_insert_event_calls_refresh_claim(self, mock_mongo_db):
        event = make_change_event("insert", claim_id=100)

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ) as mock_refresh:
            token = await _process_change_event(
                mock_mongo_db, mock_mongo_db, event
            )

        mock_refresh.assert_called_once_with(
            ai_db=mock_mongo_db,
            db=mock_mongo_db,
            claim_id=100,
            source_event_type="insert",
        )
        assert token == event["_id"]

    @pytest.mark.asyncio
    async def test_delete_event_skipped(self, mock_mongo_db):
        """Delete events are skipped (no refresh_claim call)."""
        event = make_change_event("delete")

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ) as mock_refresh:
            token = await _process_change_event(
                mock_mongo_db, mock_mongo_db, event
            )

        mock_refresh.assert_not_called()
        # Token is still returned so the listener can persist it
        assert token == event["_id"]

    @pytest.mark.asyncio
    async def test_event_with_no_claim_id_skipped(self, mock_mongo_db):
        """Events with no extractable claim_id are skipped."""
        event = make_change_event("insert", claim_id=None)

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ) as mock_refresh:
            token = await _process_change_event(
                mock_mongo_db, mock_mongo_db, event
            )

        mock_refresh.assert_not_called()
        assert token == event["_id"]

    @pytest.mark.asyncio
    async def test_update_event_calls_refresh_claim(self, mock_mongo_db):
        event = make_change_event("update", claim_id=200)

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ) as mock_refresh:
            await _process_change_event(mock_mongo_db, mock_mongo_db, event)

        mock_refresh.assert_called_once_with(
            ai_db=mock_mongo_db,
            db=mock_mongo_db,
            claim_id=200,
            source_event_type="update",
        )

    @pytest.mark.asyncio
    async def test_replace_event_calls_refresh_claim(self, mock_mongo_db):
        event = make_change_event("replace", claim_id=300)

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ) as mock_refresh:
            await _process_change_event(mock_mongo_db, mock_mongo_db, event)

        mock_refresh.assert_called_once_with(
            ai_db=mock_mongo_db,
            db=mock_mongo_db,
            claim_id=300,
            source_event_type="replace",
        )


# ---------------------------------------------------------------------------
# run_change_stream_listener — happy path
# ---------------------------------------------------------------------------


class TestListenerHappyPath:
    """Tests that the listener processes events and persists resume tokens."""

    @pytest.mark.asyncio
    async def test_processes_events_and_saves_tokens(self, mock_mongo_db):
        """The listener processes change events and saves resume tokens."""
        stop_event = asyncio.Event()
        events = [
            make_change_event("insert", claim_id=100),
            make_change_event("update", claim_id=200),
            make_change_event("replace", claim_id=300),
        ]
        # After events are consumed, the stream blocks until stop_event
        stream = FakeChangeStream(events, stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ):
            task = asyncio.create_task(
                run_change_stream_listener(ai_db, mock_mongo_db, stop_event)
            )
            # Give it time to process all events
            await asyncio.sleep(0.2)
            stop_event.set()
            await asyncio.wait_for(task, timeout=2.0)

        # Verify resume token was saved (should be the last event's token)
        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state is not None
        assert state["resume_token"] == events[2]["_id"]
        assert state["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_no_saved_token_starts_fresh(self, mock_mongo_db):
        """With no prior state, the listener starts without a resume token."""
        stop_event = asyncio.Event()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        async def set_stop_later():
            await asyncio.sleep(0.05)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(ai_db, mock_mongo_db, stop_event),
            set_stop_later(),
        )

        # watch() should have been called without resume_after
        assert len(ai_db.watch_calls) == 1
        assert "resume_after" not in ai_db.watch_calls[0]

    @pytest.mark.asyncio
    async def test_loads_saved_token_and_resumes(self, mock_mongo_db):
        """The listener loads a saved resume token and passes it to watch()."""
        # Seed a saved resume token
        saved_token = {"_data": "saved_token_abc"}
        await mock_mongo_db[worker_config.WORKER_STATE_COLLECTION].insert_one(
            {
                "_id": worker_config.WORKER_NAME,
                "resume_token": saved_token,
            }
        )

        stop_event = asyncio.Event()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        async def set_stop_later():
            await asyncio.sleep(0.05)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(ai_db, mock_mongo_db, stop_event),
            set_stop_later(),
        )

        # watch() should have been called with resume_after=saved_token
        assert len(ai_db.watch_calls) == 1
        assert ai_db.watch_calls[0]["resume_after"] == saved_token


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestListenerCancellation:
    """Tests that the listener respects stop_event for graceful shutdown."""

    @pytest.mark.asyncio
    async def test_stop_event_set_before_start(self, mock_mongo_db):
        """If stop_event is already set, the listener exits without opening a stream."""
        stop_event = asyncio.Event()
        stop_event.set()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        await run_change_stream_listener(ai_db, mock_mongo_db, stop_event)

        # Stream should NOT have been opened — stop was already set
        assert len(ai_db.watch_calls) == 0

        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self, mock_mongo_db):
        """asyncio.CancelledError propagates and sets status to stopped."""
        stop_event = asyncio.Event()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        task = asyncio.create_task(
            run_change_stream_listener(ai_db, mock_mongo_db, stop_event)
        )
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["status"] == "stopped"


# ---------------------------------------------------------------------------
# Stream restart on error
# ---------------------------------------------------------------------------


class TestListenerStreamRestart:
    """Tests that the listener restarts the stream on transient errors."""

    @pytest.mark.asyncio
    async def test_restarts_after_stream_error(self, mock_mongo_db):
        """When the stream breaks, the listener restarts with backoff."""
        stop_event = asyncio.Event()

        call_count = {"n": 0}

        def stream_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First stream: raise an error immediately
                return FakeChangeStream(
                    raise_on_iterate=ConnectionError("stream broke")
                )
            # Second stream: block until stop_event (like a healthy stream)
            return FakeChangeStream(stop_event=stop_event)

        ai_db = FakeAIDB(stream_factory=stream_factory)

        # Set stop_event after a short delay to let the restart happen
        async def set_stop_later():
            await asyncio.sleep(0.1)
            stop_event.set()

        with patch(
            "ai_analytics_worker.change_stream_listener.refresh_claim",
            new=AsyncMock(),
        ):
            await asyncio.gather(
                run_change_stream_listener(
                    ai_db,
                    mock_mongo_db,
                    stop_event,
                    restart_delay_seconds=0.01,
                    max_restarts=3,
                ),
                set_stop_later(),
            )

        # watch() was called twice (first broke, second succeeded)
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_exceeds_max_restarts_raises(self, mock_mongo_db):
        """When max_restarts is exceeded, the listener raises RuntimeError."""
        stop_event = asyncio.Event()

        def always_failing_factory():
            return FakeChangeStream(
                raise_on_iterate=ConnectionError("broken")
            )

        ai_db = FakeAIDB(stream_factory=always_failing_factory)

        with pytest.raises(RuntimeError, match="exceeded max_restarts"):
            await run_change_stream_listener(
                ai_db,
                mock_mongo_db,
                stop_event,
                restart_delay_seconds=0.001,
                max_restarts=2,
            )

        # Worker state should show error status
        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["status"] == "error"

    @pytest.mark.asyncio
    async def test_zero_max_restarts_retries_forever(self, mock_mongo_db):
        """With max_restarts=0, the listener retries indefinitely (until stop)."""
        stop_event = asyncio.Event()
        call_count = {"n": 0}

        def stream_factory():
            call_count["n"] += 1
            if call_count["n"] <= 3:
                return FakeChangeStream(
                    raise_on_iterate=ConnectionError("broken")
                )
            # 4th call: block until stop_event (healthy stream)
            return FakeChangeStream(stop_event=stop_event)

        ai_db = FakeAIDB(stream_factory=stream_factory)

        async def set_stop_later():
            await asyncio.sleep(0.2)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(
                ai_db,
                mock_mongo_db,
                stop_event,
                restart_delay_seconds=0.001,
                max_restarts=0,
            ),
            set_stop_later(),
        )

        # Should have retried at least 3 times before succeeding on 4th
        assert call_count["n"] >= 4

    @pytest.mark.asyncio
    async def test_stream_ending_on_its_own_counts_as_restart(self, mock_mongo_db):
        """A stream that ends without stop_event set is treated as a break.

        A healthy change stream blocks forever waiting for events, so end of
        iteration means the server closed the cursor (invalidate, dropped
        collection, killed cursor). If that were treated as a graceful exit,
        the outer loop would reopen the stream immediately with no backoff and
        no restart accounting — a tight loop hammering Atlas. Bounding it with
        max_restarts proves the condition is routed through the restart
        handler instead.
        """
        stop_event = asyncio.Event()
        call_count = {"n": 0}

        def always_ending_factory():
            call_count["n"] += 1
            # stop_event=None → StopAsyncIteration immediately, i.e. the
            # iterator ends on its own while stop_event is still unset.
            return FakeChangeStream()

        ai_db = FakeAIDB(stream_factory=always_ending_factory)

        with pytest.raises(RuntimeError, match="exceeded max_restarts"):
            await run_change_stream_listener(
                ai_db,
                mock_mongo_db,
                stop_event,
                restart_delay_seconds=0.001,
                max_restarts=2,
            )

        # Bounded by max_restarts rather than spinning forever.
        assert call_count["n"] == 3

        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["status"] == "error"

    @pytest.mark.asyncio
    async def test_stream_ending_on_its_own_backs_off_then_recovers(
        self, mock_mongo_db
    ):
        """A self-ending stream is retried with backoff and can recover."""
        stop_event = asyncio.Event()
        call_count = {"n": 0}

        def stream_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Ends on its own without stop_event set.
                return FakeChangeStream()
            # Healthy stream: blocks until stop_event.
            return FakeChangeStream(stop_event=stop_event)

        ai_db = FakeAIDB(stream_factory=stream_factory)

        async def set_stop_later():
            await asyncio.sleep(0.1)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(
                ai_db,
                mock_mongo_db,
                stop_event,
                restart_delay_seconds=0.001,
                max_restarts=3,
            ),
            set_stop_later(),
        )

        assert call_count["n"] == 2
        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["status"] == "stopped"


# ---------------------------------------------------------------------------
# Empty stream
# ---------------------------------------------------------------------------


class TestListenerEmptyStream:
    """Tests that an empty stream completes gracefully."""

    @pytest.mark.asyncio
    async def test_empty_stream_exits_on_stop(self, mock_mongo_db):
        """An empty stream (no events) exits when stop_event is set."""
        stop_event = asyncio.Event()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        async def set_stop_later():
            await asyncio.sleep(0.05)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(ai_db, mock_mongo_db, stop_event),
            set_stop_later(),
        )

        state = await mock_mongo_db[
            worker_config.WORKER_STATE_COLLECTION
        ].find_one({"_id": worker_config.WORKER_NAME})
        assert state["status"] == "stopped"


# ---------------------------------------------------------------------------
# Watch options
# ---------------------------------------------------------------------------


class TestWatchOptions:
    """Tests that the listener opens the stream with correct options."""

    @pytest.mark.asyncio
    async def test_watch_uses_update_lookup_full_document(self, mock_mongo_db):
        """The stream is opened with full_document='updateLookup'."""
        stop_event = asyncio.Event()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        async def set_stop_later():
            await asyncio.sleep(0.05)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(ai_db, mock_mongo_db, stop_event),
            set_stop_later(),
        )

        assert len(ai_db.watch_calls) == 1
        kwargs = ai_db.watch_calls[0]
        assert kwargs["full_document"] == "updateLookup"

    @pytest.mark.asyncio
    async def test_watch_filters_to_watched_collections(self, mock_mongo_db):
        """The pipeline filters to ai_line_items and ai_agent_conversations."""
        stop_event = asyncio.Event()
        stream = FakeChangeStream(stop_event=stop_event)
        ai_db = FakeAIDB(stream)

        async def set_stop_later():
            await asyncio.sleep(0.05)
            stop_event.set()

        await asyncio.gather(
            run_change_stream_listener(ai_db, mock_mongo_db, stop_event),
            set_stop_later(),
        )

        kwargs = ai_db.watch_calls[0]
        pipeline = kwargs["pipeline"]
        # The pipeline should contain a $match on ns.coll with the two collections
        assert len(pipeline) == 1
        match_stage = pipeline[0]
        assert "$match" in match_stage
        coll_filter = match_stage["$match"]["ns.coll"]["$in"]
        assert "ai_line_items" in coll_filter
        assert "ai_agent_conversations" in coll_filter
