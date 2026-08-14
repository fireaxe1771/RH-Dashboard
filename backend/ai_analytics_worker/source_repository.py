"""AI Analytics Worker — read-only source repository (Phase 2).

Wraps the existing read functions in ``backend/ai_analytics/mongo_repository.py``
with three concerns the underlying functions don't have:

- **Timeout enforcement** — ``WORKER_SOURCE_QUERY_TIMEOUT_MS`` cancels a stuck
  query instead of starving the event loop.
- **Retry with exponential backoff** — transient infrastructure errors
  (replica-set election, network blip, slow query) are retried up to
  ``WORKER_MAX_RETRIES`` times. Non-transient errors raise immediately.
- **Structured logging** — every query logs ``claim_id`` and ``worker_version``
  so a stuck or failing claim is traceable in production logs.

The wrapper does NOT reimplement the queries. It imports and calls the existing
functions, preserving the verified query patterns from Phase 0.

Source: RecoveryHub_AI MongoDB (read-only).
Destination: none.
Architectural constraints:
- Never mutates RecoveryHub_AI collections.
- Imports collection name constants from ``mongo_repository.py`` (DRY).
- A query that exceeds its timeout is classified as transient and retried.
- A non-transient error is raised immediately without retry.
- ``asyncio.CancelledError`` (external cancellation, e.g. lifespan shutdown)
  propagates immediately without retry.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    ExecutionTimeout,
    NetworkTimeout,
    ServerSelectionTimeoutError,
)

from ai_analytics.mongo_repository import (
    get_ai_line_items_for_claim,
    get_agent_conversations_for_claim,
)

from .config import worker_config

logger = logging.getLogger(__name__)

# Errors that indicate a transient infrastructure condition worth retrying.
# A subsequent attempt may hit a different replica set member or the
# condition may have resolved. PyMongo raises these for connection-level
# failures, not for data-level errors.
_TRANSIENT_ERRORS: Tuple[Type[BaseException], ...] = (
    ServerSelectionTimeoutError,
    NetworkTimeout,
    AutoReconnect,
    ConnectionFailure,
    ExecutionTimeout,
    asyncio.TimeoutError,  # raised by asyncio.wait_for when our timeout fires
)

# Base delay (seconds) for the first retry backoff. Subsequent retries double
# this value, capped at _MAX_BACKOFF_SECONDS. The base is deliberately small
# so that a single transient blip costs <1s of total stall time.
_RETRY_BASE_DELAY_SECONDS = 0.1
_MAX_BACKOFF_SECONDS = 2.0


def _is_transient(exc: BaseException) -> bool:
    """Return True if ``exc`` is a transient error worth retrying."""
    return isinstance(exc, _TRANSIENT_ERRORS)


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff delay in seconds for the given attempt (1-based).

    Attempt 1 → 0.1s, attempt 2 → 0.2s, attempt 3 → 0.4s, etc.
    Capped at ``_MAX_BACKOFF_SECONDS`` so a multi-retry failure doesn't stall
    the worker for tens of seconds.
    """
    delay = _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
    return min(delay, _MAX_BACKOFF_SECONDS)


async def _with_retry(
    operation_name: str,
    claim_id: int,
    coroutine_factory: Callable[[], Any],
) -> Any:
    """Execute a coroutine with timeout and retry.

    Arguments:
        operation_name: human-readable name for logging (e.g. "ai_line_items").
        claim_id: the claim being fetched (for structured logging).
        coroutine_factory: a zero-argument callable that returns a fresh
            coroutine to await. Called once per attempt so retries get a
            clean coroutine rather than a reused (and possibly exhausted) one.

    Returns:
        The result of the awaited coroutine.

    Raises:
        The last exception if all attempts fail with transient errors.
        The original exception immediately for non-transient errors.
        ``asyncio.CancelledError`` immediately if the caller is cancelled
        (never retried, never swallowed).
    """
    # Note: worker_config.max_retries is the max total attempts, not retries
    # after the first attempt. A value of 3 means attempts 1, 2, 3
    # (1 initial + 2 retries). See config.WORKER_MAX_RETRIES.
    max_attempts = worker_config.max_retries
    timeout_seconds = worker_config.source_query_timeout_ms / 1000.0
    last_exc: Optional[BaseException] = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                coroutine_factory(),
                timeout=timeout_seconds,
            )
            if attempt > 1:
                logger.info(
                    "worker source query succeeded on attempt %d/%d "
                    "(operation=%s, claim_id=%d, worker_version=%s).",
                    attempt,
                    max_attempts,
                    operation_name,
                    claim_id,
                    worker_config.worker_version,
                )
            return result
        except asyncio.CancelledError:
            # External cancellation (e.g. lifespan shutdown) — propagate
            # immediately. Do not retry, do not swallow.
            logger.info(
                "worker source query cancelled (operation=%s, claim_id=%d).",
                operation_name,
                claim_id,
            )
            raise
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc):
                # Non-transient error (e.g. OperationFailure, ValueError) —
                # retrying won't help. Raise immediately.
                logger.error(
                    "worker source query failed with non-transient error "
                    "(operation=%s, claim_id=%d, worker_version=%s, "
                    "error_type=%s, error=%r).",
                    operation_name,
                    claim_id,
                    worker_config.worker_version,
                    type(exc).__name__,
                    exc,
                )
                raise

            if attempt < max_attempts:
                delay = _backoff_delay(attempt)
                logger.warning(
                    "worker source query failed with transient error on "
                    "attempt %d/%d (operation=%s, claim_id=%d, "
                    "worker_version=%s, error_type=%s, error=%r); "
                    "retrying in %.2fs.",
                    attempt,
                    max_attempts,
                    operation_name,
                    claim_id,
                    worker_config.worker_version,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "worker source query failed after %d/%d attempts "
                    "(operation=%s, claim_id=%d, worker_version=%s, "
                    "last_error_type=%s, last_error=%r).",
                    attempt,
                    max_attempts,
                    operation_name,
                    claim_id,
                    worker_config.worker_version,
                    type(exc).__name__,
                    exc,
                )

    # All attempts exhausted — raise the last transient exception.
    assert last_exc is not None
    raise last_exc


async def get_ai_line_items_for_claim_with_retry(
    ai_db: Any,
    claim_id: int,
) -> Optional[Dict[str, Any]]:
    """Fetch a single ai_line_items document with timeout and retry.

    Wraps ``mongo_repository.get_ai_line_items_for_claim``.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle.
        claim_id: the claim ID to fetch.

    Returns:
        The ai_line_items document, or ``None`` if not found.

    Raises:
        Transient errors are retried up to ``WORKER_MAX_RETRIES`` times with
        exponential backoff. Non-transient errors propagate immediately.
        If all attempts fail with transient errors, the last exception is
        raised.
    """
    return await _with_retry(
        operation_name="ai_line_items",
        claim_id=claim_id,
        coroutine_factory=lambda: get_ai_line_items_for_claim(ai_db, claim_id),
    )


async def get_agent_conversations_for_claim_with_retry(
    ai_db: Any,
    claim_id: int,
) -> List[Dict[str, Any]]:
    """Fetch agent conversations for a claim with timeout and retry.

    Wraps ``mongo_repository.get_agent_conversations_for_claim``.

    Arguments:
        ai_db: the RecoveryHub_AI Motor database handle.
        claim_id: the claim ID to fetch conversations for.

    Returns:
        List of conversation documents, sorted chronologically.

    Raises:
        Transient errors are retried up to ``WORKER_MAX_RETRIES`` times with
        exponential backoff. Non-transient errors propagate immediately.
        If all attempts fail with transient errors, the last exception is
        raised.
    """
    return await _with_retry(
        operation_name="agent_conversations",
        claim_id=claim_id,
        coroutine_factory=lambda: get_agent_conversations_for_claim(ai_db, claim_id),
    )
