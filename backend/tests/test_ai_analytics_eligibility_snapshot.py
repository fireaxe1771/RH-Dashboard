"""Projection-mode AI eligibility must not depend on the operational AI
Mongo fee configuration being reachable (Devin Review, PR #39)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from ai_analytics import eligibility_snapshot as snap
from ai_analytics_worker.config import worker_config

PART = {1: {"uses_ai": True, "ai_mode": "auto"}}


async def _seed(db, age_seconds=0):
    await db[worker_config.AI_ELIGIBILITY_SNAPSHOT_COLLECTION].insert_one({
        "_id": worker_config.AI_ELIGIBILITY_SNAPSHOT_ID,
        "participation": {"1": PART[1]},
        "refreshed_at": datetime.now(UTC) - timedelta(seconds=age_seconds),
    })


@pytest.mark.asyncio
async def test_projection_mode_serves_fresh_snapshot_without_source(mock_mongo_db):
    await _seed(mock_mongo_db)
    fetch = AsyncMock(side_effect=RuntimeError("ai mongo down"))
    result, status = await snap.resolve_participation(
        object(), mock_mongo_db, fetch=fetch, prefer_snapshot=True
    )
    assert result == PART
    assert status == snap.STATUS_AVAILABLE
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_snapshot_refreshes_from_source_and_rewrites(mock_mongo_db):
    await _seed(mock_mongo_db, age_seconds=worker_config.AI_ELIGIBILITY_SNAPSHOT_TTL_SECONDS + 5)
    fetch = AsyncMock(return_value={2: {"uses_ai": True}})
    result, status = await snap.resolve_participation(
        object(), mock_mongo_db, fetch=fetch, prefer_snapshot=True
    )
    assert result == {2: {"uses_ai": True}}
    assert status == snap.STATUS_AVAILABLE
    fetch.assert_awaited_once()
    stored, _ = await snap.read_snapshot(mock_mongo_db)
    assert stored == {2: {"uses_ai": True}}


@pytest.mark.asyncio
async def test_source_failure_falls_back_to_stale_snapshot(mock_mongo_db):
    await _seed(mock_mongo_db, age_seconds=worker_config.AI_ELIGIBILITY_SNAPSHOT_TTL_SECONDS + 5)
    fetch = AsyncMock(return_value=None)
    result, status = await snap.resolve_participation(
        object(), mock_mongo_db, fetch=fetch, prefer_snapshot=True
    )
    assert result == PART
    assert status == snap.STATUS_STALE


@pytest.mark.asyncio
async def test_direct_mode_reads_source_and_writes_snapshot(mock_mongo_db):
    await _seed(mock_mongo_db)
    fetch = AsyncMock(return_value={3: {"uses_ai": False}})
    result, status = await snap.resolve_participation(
        object(), mock_mongo_db, fetch=fetch, prefer_snapshot=False
    )
    assert result == {3: {"uses_ai": False}}
    fetch.assert_awaited_once()
    stored, _ = await snap.read_snapshot(mock_mongo_db)
    assert stored == {3: {"uses_ai": False}}


@pytest.mark.asyncio
async def test_no_snapshot_and_no_source_is_unavailable(mock_mongo_db):
    fetch = AsyncMock(return_value=None)
    result, status = await snap.resolve_participation(
        object(), mock_mongo_db, fetch=fetch, prefer_snapshot=True
    )
    assert result is None
    assert status == snap.STATUS_UNAVAILABLE
