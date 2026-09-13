"""Tests for ai_analytics_worker.leader_election and the runtime campaign.

Covers the MongoDB lease primitives (acquire / mutual exclusion / renew /
expire / release) and the runtime campaign loop that gates run_worker behind
the lease for multi-process (uvicorn --workers N) deployments.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_analytics_worker import runtime
from ai_analytics_worker.config import worker_config
from ai_analytics_worker.leader_election import (
    LEADER_LOCK_ID,
    release_leadership,
    renew_leadership,
    try_acquire_leadership,
)

LEASE = 60


def _utcnow_naive() -> datetime:
    """mongomock round-trips datetimes as tz-naive, so compare naive."""
    return datetime.now(UTC).replace(tzinfo=None)


async def _read_lease(db):
    return await db[worker_config.WORKER_STATE_COLLECTION].find_one(
        {"_id": LEADER_LOCK_ID}
    )


class TestAcquire:
    @pytest.mark.asyncio
    async def test_first_holder_acquires(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        doc = await _read_lease(mock_mongo_db)
        assert doc["holder_id"] == "holder-a"
        assert doc["lease_expires_at"] > _utcnow_naive()
        assert "acquired_at" in doc

    @pytest.mark.asyncio
    async def test_second_holder_rejected_while_lease_valid(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        assert not await try_acquire_leadership(mock_mongo_db, "holder-b", LEASE)
        doc = await _read_lease(mock_mongo_db)
        assert doc["holder_id"] == "holder-a"

    @pytest.mark.asyncio
    async def test_expired_lease_can_be_taken_over(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        # Force the lease into the past.
        await mock_mongo_db[worker_config.WORKER_STATE_COLLECTION].update_one(
            {"_id": LEADER_LOCK_ID},
            {"$set": {"lease_expires_at": _utcnow_naive() - timedelta(seconds=1)}},
        )
        assert await try_acquire_leadership(mock_mongo_db, "holder-b", LEASE)
        doc = await _read_lease(mock_mongo_db)
        assert doc["holder_id"] == "holder-b"

    @pytest.mark.asyncio
    async def test_same_holder_reacquires(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)


class TestRenew:
    @pytest.mark.asyncio
    async def test_renew_extends_lease(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        before = (await _read_lease(mock_mongo_db))["lease_expires_at"]
        assert await renew_leadership(mock_mongo_db, "holder-a", LEASE * 10)
        after = (await _read_lease(mock_mongo_db))["lease_expires_at"]
        assert after > before

    @pytest.mark.asyncio
    async def test_renew_fails_for_non_holder(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        assert not await renew_leadership(mock_mongo_db, "holder-b", LEASE)

    @pytest.mark.asyncio
    async def test_renew_fails_when_no_lease(self, mock_mongo_db):
        assert not await renew_leadership(mock_mongo_db, "holder-a", LEASE)


class TestRelease:
    @pytest.mark.asyncio
    async def test_release_expires_lease_for_peer_takeover(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        await release_leadership(mock_mongo_db, "holder-a")
        # A peer can now acquire without waiting out the TTL.
        assert await try_acquire_leadership(mock_mongo_db, "holder-b", LEASE)

    @pytest.mark.asyncio
    async def test_release_by_non_holder_is_noop(self, mock_mongo_db):
        assert await try_acquire_leadership(mock_mongo_db, "holder-a", LEASE)
        await release_leadership(mock_mongo_db, "holder-b")
        doc = await _read_lease(mock_mongo_db)
        assert doc["lease_expires_at"] > _utcnow_naive()
        assert not await try_acquire_leadership(mock_mongo_db, "holder-b", LEASE)


class TestCampaignLoop:
    """The campaign task gates run_worker behind the lease."""

    @pytest.fixture
    def runtime_state(self):
        """Reset runtime module state around each test."""
        runtime._worker_task = None
        runtime._worker_stop_event = None
        runtime._is_worker_leader = False
        runtime._worker_holder_id = None
        yield
        runtime._worker_task = None
        runtime._worker_stop_event = None
        runtime._is_worker_leader = False
        runtime._worker_holder_id = None

    @pytest.mark.asyncio
    async def test_leader_runs_worker_and_releases_on_stop(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        ran_worker = asyncio.Event()

        async def fake_run_worker(stop_event, ai_db=None, db=None):
            ran_worker.set()
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)

        stop = asyncio.Event()
        task = asyncio.create_task(
            runtime._run_leader_campaign(stop, mock_mongo_db, mock_mongo_db)
        )
        await asyncio.wait_for(ran_worker.wait(), timeout=5)
        assert runtime.is_worker_leader()

        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert not runtime.is_worker_leader()

        # Lease released — a new holder can acquire immediately.
        assert await try_acquire_leadership(mock_mongo_db, "holder-x", LEASE)

    @pytest.mark.asyncio
    async def test_non_leader_does_not_run_worker(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        # Pre-seed a valid lease held by someone else.
        assert await try_acquire_leadership(mock_mongo_db, "other-host:1:x", LEASE)

        ran_worker = asyncio.Event()

        async def fake_run_worker(stop_event, ai_db=None, db=None):
            ran_worker.set()
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)

        stop = asyncio.Event()
        task = asyncio.create_task(
            runtime._run_leader_campaign(stop, mock_mongo_db, mock_mongo_db)
        )
        await asyncio.sleep(0.3)
        assert not ran_worker.is_set()
        assert not runtime.is_worker_leader()

        stop.set()
        await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_lease_loss_stops_worker(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        ran_worker = asyncio.Event()
        worker_stopped = asyncio.Event()

        async def fake_run_worker(stop_event, ai_db=None, db=None):
            ran_worker.set()
            await stop_event.wait()
            worker_stopped.set()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)

        # Make renewals fast so the loop notices the loss quickly. The
        # accessor is a read-only property, so patch it on the class —
        # before the campaign starts, since the in-flight sleep already
        # captured the previous interval.
        monkeypatch.setattr(
            type(worker_config),
            "leader_renew_interval_seconds",
            property(lambda self: 0.05),
        )

        stop = asyncio.Event()
        task = asyncio.create_task(
            runtime._run_leader_campaign(stop, mock_mongo_db, mock_mongo_db)
        )
        await asyncio.wait_for(ran_worker.wait(), timeout=5)

        # Simulate a takeover: point the lease at another holder.
        await mock_mongo_db[worker_config.WORKER_STATE_COLLECTION].update_one(
            {"_id": LEADER_LOCK_ID},
            {"$set": {"holder_id": "usurper"}},
        )

        await asyncio.wait_for(worker_stopped.wait(), timeout=5)
        # The flag clears in the campaign's finally after worker teardown.
        for _ in range(100):
            if not runtime.is_worker_leader():
                break
            await asyncio.sleep(0.05)
        assert not runtime.is_worker_leader()

        stop.set()
        await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_leader_hook_runs_only_for_leader(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """``on_leader_acquired`` fires in the process that wins the lease
        and never in a candidate that does not — the startup backfill check
        must not run once per uvicorn worker."""
        async def fake_run_worker(stop_event, ai_db=None, db=None):
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)

        hook_calls = 0
        hook_ran = asyncio.Event()

        async def hook():
            nonlocal hook_calls
            hook_calls += 1
            hook_ran.set()

        stop = asyncio.Event()
        leader = asyncio.create_task(
            runtime._run_leader_campaign(
                stop, mock_mongo_db, mock_mongo_db, on_leader_acquired=hook
            )
        )
        await asyncio.wait_for(hook_ran.wait(), timeout=5)
        assert hook_calls == 1

        # A second campaign in the same event loop stands in for another
        # process: the lease is taken, so its hook must not fire.
        candidate_calls = 0

        async def candidate_hook():
            nonlocal candidate_calls
            candidate_calls += 1

        candidate_stop = asyncio.Event()
        candidate = asyncio.create_task(
            runtime._run_leader_campaign(
                candidate_stop, mock_mongo_db, mock_mongo_db,
                on_leader_acquired=candidate_hook,
            )
        )
        await asyncio.sleep(0.3)
        assert candidate_calls == 0

        candidate_stop.set()
        stop.set()
        await asyncio.wait_for(asyncio.gather(leader, candidate), timeout=5)
        assert hook_calls == 1

    @pytest.mark.asyncio
    async def test_failed_worker_term_backs_off_before_recampaign(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """A run_worker that dies on its own must not be restarted in a
        tight loop: successive terms are spaced by a growing backoff."""
        starts: list = []

        async def crashing_run_worker(stop_event, ai_db=None, db=None):
            starts.append(asyncio.get_running_loop().time())
            raise RuntimeError("boom")

        monkeypatch.setattr(runtime, "run_worker", crashing_run_worker)
        monkeypatch.setattr(
            type(worker_config),
            "leader_campaign_seconds",
            property(lambda self: 0.1),
        )
        monkeypatch.setattr(
            type(worker_config),
            "leader_renew_interval_seconds",
            property(lambda self: 0.05),
        )

        stop = asyncio.Event()
        task = asyncio.create_task(
            runtime._run_leader_campaign(stop, mock_mongo_db, mock_mongo_db)
        )
        for _ in range(200):
            if len(starts) >= 3:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

        assert len(starts) >= 3
        gap1 = starts[1] - starts[0]
        gap2 = starts[2] - starts[1]
        # First retry waits at least one campaign interval; the next waits
        # roughly twice as long (exponential), so there is no hot loop.
        assert gap1 >= 0.09
        assert gap2 >= 0.18
        assert not runtime.is_worker_leader()
