"""AI Analytics Worker — Change Stream listener (Phase 5/6).

Watches the ``ai_line_items`` and ``ai_agent_conversations`` collections in
the RecoveryHub_AI MongoDB database for relevant changes. Extracts the
affected ``claim_id`` from each change event, **enqueues** it into the
``ClaimQueue`` (Phase 6 — the queue consumer calls ``refresh_claim``), and
persists the resume token so the worker can resume after restart without
losing events.

Algorithm:
1. Load the saved resume token from ``ai_analytics_worker_state`` (if any).
2. Open a change stream on the RecoveryHub_AI database, filtered to the two
   watched collections, with ``fullDocument: 'updateLookup'`` so updates
   carry the full document (not just the changed fields).
3. For each change event:
   a. Extract ``claim_id`` from the event (``fullDocument.claim_id`` for
      insert/replace/update, or skip delete events — reconciliation in
      Phase 7 handles stale projections from deletes).
   b. Enqueue ``claim_id`` into the ``ClaimQueue``. The queue consumer
      (Phase 6) calls ``refresh_claim`` with a debounce window to coalesce
      bursts. Errors are dead-lettered inside ``refresh_claim``.
   c. Persist the event's resume token to ``ai_analytics_worker_state``.
   d. Yield control to the event loop every ``max_claims_per_cycle`` events
      (Section 1.1.5 — event-loop starvation guard).
4. If the stream breaks (transient error), restart it with the last saved
   resume token after a backoff delay. Up to ``change_stream_max_restarts``
   consecutive failures (0 = retry forever).
5. Respect ``stop_event`` for graceful cancellation — close the stream,
   persist the final token, and exit.

Source: RecoveryHub_AI MongoDB (read-only via Motor async client).
Destination: ``ai_analytics_worker_state`` (resume token, health). The
actual projection writes go through the queue consumer (Phase 6) which
calls ``claim_refresh``.
Architectural constraints:
- Never writes to operational AI collections (source is read-only).
- Never blocks the FastAPI event loop — all I/O is async.
- Cancellable within 5 seconds (close stream, persist token, exit).
- One bad event never stops the listener — enqueueing is infallible.
- Resume token persisted after each event so restart loses no events.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from ai_analytics.mongo_repository import (
    AI_LINE_ITEMS_COLLECTION,
    AGENT_CONVERSATIONS_COLLECTION,
)

from .config import worker_config
from .projection_repository import get_worker_state, update_worker_state
from .queue import ClaimQueue

logger = logging.getLogger(__name__)


# Change event operation types we care about. ``delete`` events don't carry
# ``fullDocument`` so we can't extract ``claim_id`` from them — the listener
# skips deletes and lets Phase 7 reconciliation handle stale projections.
_WATCHED_OPERATIONS = {"insert", "update", "replace"}

# Cap for the exponential backoff delay between stream restarts (seconds).
# Even with max_restarts=0 (retry forever), each delay is capped so the
# listener doesn't sleep for minutes during a prolonged outage.
_MAX_RESTART_DELAY_SECONDS = 30.0

# Max seconds to spend persisting the final "stopped" state on the
# cancellation path. The lifespan shutdown contract (Section 1.1.4) gives the
# worker 5s total, and a cancelled task's write may itself hang, so this write
# is bounded and its failure is non-fatal.
_SHUTDOWN_STATE_WRITE_TIMEOUT_SECONDS = 2.0


class _StreamEndedUnexpectedly(RuntimeError):
    """Raised when the change stream iterator ends without ``stop_event`` set.

    A healthy change stream blocks indefinitely waiting for events, so a
    normal end of iteration means the server closed the cursor (an
    ``invalidate`` event, a dropped collection/database, or a killed cursor).
    Raising this routes the condition through the restart handler so it gets
    backoff and ``max_restarts`` accounting instead of being reopened in a
    tight, unthrottled loop.
    """

# Change stream pipeline: watch only the two source collections. Applied as
# a ``$match`` on ``ns.coll`` so the stream doesn't deliver events from
# other collections in the same database.
_CHANGE_STREAM_PIPELINE = [
    {
        "$match": {
            "ns.coll": {
                "$in": [AI_LINE_ITEMS_COLLECTION, AGENT_CONVERSATIONS_COLLECTION],
            }
        }
    }
]


def _extract_claim_id(change_event: Dict[str, Any]) -> Optional[int]:
    """Extract the ``claim_id`` from a MongoDB change stream event.

    For ``insert``, ``replace``, and ``update`` events, the full document is
    available (we open the stream with ``fullDocument: 'updateLookup'``).
    The ``claim_id`` field is at the top level of the source document.

    Arguments:
        change_event: the raw change stream event dict from Motor.

    Returns:
        The integer ``claim_id``, or ``None`` if it can't be extracted
        (missing field, non-numeric value, delete event, etc.).
    """
    full_doc = change_event.get("fullDocument")
    if full_doc is None:
        # delete events or events where fullDocument wasn't available.
        return None

    raw_claim_id = full_doc.get("claim_id")
    if raw_claim_id is None:
        return None

    try:
        return int(raw_claim_id)
    except (ValueError, TypeError):
        return None


def _extract_resume_token(change_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract the resume token from a change stream event.

    The resume token is in the ``_id`` field of the change event document.
    It's an opaque BSON document that can be passed to ``resume_after`` to
    continue the stream from this point.

    Arguments:
        change_event: the raw change stream event dict from Motor.

    Returns:
        The resume token dict, or ``None`` if not present.
    """
    return change_event.get("_id")


async def _load_resume_token(db: Any) -> Optional[Dict[str, Any]]:
    """Load the saved resume token from ``ai_analytics_worker_state``.

    Arguments:
        db: the dashboard-owned Motor database handle.

    Returns:
        The saved resume token dict, or ``None`` if no state exists yet
        (first run).
    """
    state = await get_worker_state(db, worker_config.WORKER_NAME)
    if state is None:
        return None
    return state.get("resume_token")


async def _save_resume_token(
    db: Any,
    resume_token: Dict[str, Any],
) -> None:
    """Persist the resume token to ``ai_analytics_worker_state``.

    Also updates ``last_checkpoint_at`` and ``last_successful_event_at`` so
    the health/operations dashboard can show how recently the worker
    processed an event.

    Arguments:
        db: the dashboard-owned Motor database handle.
        resume_token: the resume token dict from the last processed event.
    """
    now = datetime.now(UTC)
    await update_worker_state(
        db,
        worker_config.WORKER_NAME,
        {
            "resume_token": resume_token,
            "last_checkpoint_at": now,
            "last_successful_event_at": now,
            "status": "running",
        },
    )


async def _process_change_event(
    queue: ClaimQueue,
    change_event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Process a single change stream event.

    Extracts the ``claim_id``, enqueues it into the ``ClaimQueue`` (Phase 6),
    and returns the resume token from the event so the caller can persist it.

    Arguments:
        queue: the ``ClaimQueue`` to enqueue the claim_id into.
        change_event: the raw change stream event dict.

    Returns:
        The resume token from this event (to be persisted), or ``None``
        if the event was skipped (delete, no claim_id) — in which case the
        caller should still persist the token from the event.
    """
    operation_type = change_event.get("operationType", "unknown")

    if operation_type not in _WATCHED_OPERATIONS:
        # Skip delete/drop/invalidate/etc. — reconciliation handles stale
        # projections from deletes (Phase 7).
        logger.debug(
            "Change stream: skipping %s event (not in watched operations).",
            operation_type,
        )
        return _extract_resume_token(change_event)

    claim_id = _extract_claim_id(change_event)
    if claim_id is None:
        logger.warning(
            "Change stream: %s event has no extractable claim_id; skipping.",
            operation_type,
        )
        return _extract_resume_token(change_event)

    # Enqueue the claim for refresh via the debounce queue (Phase 6).
    # The queue consumer calls refresh_claim with a debounce window to
    # coalesce bursts of updates to the same claim.
    queue.enqueue(claim_id)

    return _extract_resume_token(change_event)


async def run_change_stream_listener(
    ai_db: Any,
    db: Any,
    stop_event: asyncio.Event,
    queue: ClaimQueue,
    max_claims_per_cycle: Optional[int] = None,
    restart_delay_seconds: Optional[float] = None,
    max_restarts: Optional[int] = None,
) -> None:
    """Run the change stream listener until ``stop_event`` is set or cancelled.

    Opens a change stream on the RecoveryHub_AI database, enqueues claim_ids
    into the queue, and persists resume tokens. If the stream breaks,
    restarts with the last saved token after a backoff delay.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle (read-only).
        db: the dashboard-owned Motor database handle (writes — resume token).
        stop_event: an ``asyncio.Event`` that the caller sets to request
            graceful shutdown. Checked between events and during restart
            backoff.
        queue: the ``ClaimQueue`` to enqueue claim_ids into (Phase 6).
        max_claims_per_cycle: max events to process before yielding control
            to the event loop. Defaults to ``worker_config.max_claims_per_cycle``.
        restart_delay_seconds: base delay between stream restart attempts.
            Defaults to ``worker_config.change_stream_restart_delay_seconds``.
            Doubled on each consecutive failure, capped at 30s.
        max_restarts: max consecutive restart attempts before giving up.
            Defaults to ``worker_config.change_stream_max_restarts``.
            0 means retry forever.

    Side effects:
        - Enqueues claim_ids into ``queue`` (consumer processes them).
        - Persists resume tokens and checkpoint timestamps to
          ``ai_analytics_worker_state``.
        - Updates ``ai_analytics_worker_state.status`` to ``"running"`` on
          start, ``"stopped"`` on graceful exit.

    Raises:
        ``asyncio.CancelledError``: if the caller cancels the task.
        ``RuntimeError``: if the stream breaks and ``max_restarts`` is
            exceeded (only when max_restarts > 0).
    """
    if max_claims_per_cycle is None:
        max_claims_per_cycle = worker_config.max_claims_per_cycle
    if restart_delay_seconds is None:
        restart_delay_seconds = worker_config.change_stream_restart_delay_seconds
    if max_restarts is None:
        max_restarts = worker_config.change_stream_max_restarts

    # Load the saved resume token so we can resume after restart.
    resume_token = await _load_resume_token(db)
    if resume_token is not None:
        logger.info(
            "Change stream listener: resuming from saved token "
            "(worker=%s).",
            worker_config.WORKER_NAME,
        )
    else:
        logger.info(
            "Change stream listener: no saved token; starting fresh "
            "(worker=%s).",
            worker_config.WORKER_NAME,
        )

    await update_worker_state(
        db,
        worker_config.WORKER_NAME,
        {"status": "running", "last_started_at": datetime.now(UTC)},
    )

    consecutive_restarts = 0

    try:
        while not stop_event.is_set():
            try:
                resume_token = await _stream_loop(
                    ai_db=ai_db,
                    db=db,
                    stop_event=stop_event,
                    resume_token=resume_token,
                    max_claims_per_cycle=max_claims_per_cycle,
                    queue=queue,
                )
                if not stop_event.is_set():
                    # The iterator ended on its own. A healthy stream blocks
                    # forever waiting for events, so this means the server
                    # closed the cursor (invalidate, dropped collection,
                    # killed cursor). Route it through the restart handler so
                    # it gets backoff instead of an immediate tight reopen.
                    raise _StreamEndedUnexpectedly(
                        "change stream iterator ended without stop_event set"
                    )
                # stop_event was set — graceful shutdown. Reset the restart
                # counter since the stream didn't break.
                consecutive_restarts = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Stream broke (transient error, network, replica-set
                # election, etc.). Restart with backoff.
                consecutive_restarts += 1
                delay = min(
                    restart_delay_seconds * (2 ** (consecutive_restarts - 1)),
                    _MAX_RESTART_DELAY_SECONDS,
                )

                if max_restarts > 0 and consecutive_restarts > max_restarts:
                    logger.error(
                        "Change stream listener: exceeded max_restarts=%d; "
                        "giving up (last_error=%s).",
                        max_restarts,
                        type(exc).__name__,
                    )
                    await update_worker_state(
                        db,
                        worker_config.WORKER_NAME,
                        {
                            "status": "error",
                            "last_error": f"{type(exc).__name__}: {exc}",
                            "consecutive_error_count": consecutive_restarts,
                        },
                    )
                    raise RuntimeError(
                        f"Change stream listener exceeded max_restarts="
                        f"{max_restarts} (last_error={type(exc).__name__})"
                    ) from exc

                logger.warning(
                    "Change stream listener: stream broke "
                    "(restart=%d, delay=%.1fs, error_type=%s, error=%r); "
                    "restarting with saved token.",
                    consecutive_restarts,
                    delay,
                    type(exc).__name__,
                    exc,
                )
                await update_worker_state(
                    db,
                    worker_config.WORKER_NAME,
                    {
                        "status": "reconnecting",
                        "last_error": f"{type(exc).__name__}: {exc}",
                        "consecutive_error_count": consecutive_restarts,
                    },
                )
                await asyncio.sleep(delay)

        # Graceful shutdown via stop_event.
        await update_worker_state(
            db,
            worker_config.WORKER_NAME,
            {"status": "stopped", "last_completed_at": datetime.now(UTC)},
        )
        logger.info("Change stream listener: stopped via stop_event.")

    except asyncio.CancelledError:
        # Cancellation — try to persist stopped state, then re-raise.
        # This await runs inside an already-cancelled task, so it can be
        # interrupted or hang; it is shielded, time-bounded, and its failure
        # is non-fatal so shutdown never blocks past the 5s contract.
        try:
            await asyncio.wait_for(
                asyncio.shield(
                    update_worker_state(
                        db,
                        worker_config.WORKER_NAME,
                        {
                            "status": "stopped",
                            "last_completed_at": datetime.now(UTC),
                        },
                    )
                ),
                timeout=_SHUTDOWN_STATE_WRITE_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning(
                "Change stream listener: could not persist stopped state "
                "during cancellation."
            )
        except Exception:
            logger.warning(
                "Change stream listener: failed to persist stopped state "
                "during cancellation.",
                exc_info=True,
            )
        logger.info("Change stream listener: cancelled; shutting down.")
        raise


async def _stream_loop(
    ai_db: Any,
    db: Any,
    stop_event: asyncio.Event,
    resume_token: Optional[Dict[str, Any]],
    max_claims_per_cycle: int,
    queue: ClaimQueue,
) -> Optional[Dict[str, Any]]:
    """Open and iterate the change stream until ``stop_event`` is set.

    Returns the last resume token. A return with ``stop_event`` set is a
    graceful exit; a return with it unset means the server closed the cursor,
    which the caller treats as a stream break. Raises if the stream breaks
    outright (caller handles restart with backoff).

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle.
        db: the dashboard-owned Motor database handle.
        stop_event: checked between events for cancellation.
        resume_token: token to resume from, or None to start fresh.
        max_claims_per_cycle: events to process before yielding to the
            event loop.

    Returns:
        The last processed resume token (for the caller to persist on
        graceful exit).
    """
    # Open the change stream. ``fullDocument: 'updateLookup'`` makes update
    # events carry the full current document so we can extract claim_id
    # without a separate query.
    watch_kwargs: Dict[str, Any] = {
        "pipeline": _CHANGE_STREAM_PIPELINE,
        "full_document": "updateLookup",
    }
    if resume_token is not None:
        watch_kwargs["resume_after"] = resume_token

    change_stream = ai_db.watch(**watch_kwargs)

    events_since_yield = 0

    try:
        # ``async for`` iterates the async change stream. Each iteration
        # yields one change event. The await inside the async-for naturally
        # yields control to the event loop while waiting for the next event.
        async for change_event in change_stream:
            if stop_event.is_set():
                # Graceful shutdown — close the stream and return.
                break

            token = await _process_change_event(queue, change_event)

            if token is not None:
                resume_token = token
                await _save_resume_token(db, resume_token)

            events_since_yield += 1
            if events_since_yield >= max_claims_per_cycle:
                # Yield control to the event loop so the FastAPI process
                # doesn't become unresponsive during a burst of events.
                await asyncio.sleep(0)
                events_since_yield = 0

    finally:
        # Close the change stream to release the server-side cursor.
        # Motor's change stream objects support ``close()`` as a coroutine.
        close_method = getattr(change_stream, "close", None)
        if close_method is not None:
            result = close_method()
            if asyncio.iscoroutine(result):
                await result

    return resume_token
