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
    BACKFILL_LOCK_ID,
    LEADER_LOCK_ID,
    is_lease_held,
    is_worker_enabled,
    release_leadership,
    release_lease,
    renew_leadership,
    set_worker_enabled,
    try_acquire_leadership,
    try_acquire_lease,
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
        runtime._backfill_task = None
        runtime._backfill_stop_event = None
        yield
        runtime._worker_task = None
        runtime._worker_stop_event = None
        runtime._is_worker_leader = False
        runtime._worker_holder_id = None
        runtime._backfill_task = None
        runtime._backfill_stop_event = None

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

    @pytest.mark.asyncio
    async def test_lease_loss_cancels_leader_hook_and_backfill(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """Losing the lease tears down the leader hook and any backfill it
        started before the lease is released, so a successor never scans
        concurrently with the previous leader."""
        async def fake_run_worker(stop_event, ai_db=None, db=None):
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)
        monkeypatch.setattr(
            type(worker_config),
            "leader_renew_interval_seconds",
            property(lambda self: 0.05),
        )

        backfill_started = asyncio.Event()
        backfill_stopped = asyncio.Event()

        async def fake_run_backfill(ai_db, db, stop_event=None, **_):
            backfill_started.set()
            try:
                await stop_event.wait()
            finally:
                backfill_stopped.set()
            raise asyncio.CancelledError

        monkeypatch.setattr(runtime, "run_backfill", fake_run_backfill)

        import database
        monkeypatch.setattr(database.db_manager, "db", mock_mongo_db)
        monkeypatch.setattr(database.db_manager, "ai_db", mock_mongo_db)

        async def hook():
            await runtime.start_backfill()

        stop = asyncio.Event()
        task = asyncio.create_task(
            runtime._run_leader_campaign(
                stop, mock_mongo_db, mock_mongo_db, on_leader_acquired=hook
            )
        )
        await asyncio.wait_for(backfill_started.wait(), timeout=5)
        assert runtime.is_backfill_running()

        await mock_mongo_db[worker_config.WORKER_STATE_COLLECTION].update_one(
            {"_id": LEADER_LOCK_ID},
            {"$set": {"holder_id": "usurper"}},
        )

        await asyncio.wait_for(backfill_stopped.wait(), timeout=5)
        for _ in range(100):
            if not runtime.is_backfill_running():
                break
            await asyncio.sleep(0.05)
        assert not runtime.is_backfill_running()
        assert not runtime.is_worker_leader()

        stop.set()
        await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_start_worker_reuses_registered_leader_hook(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """A restart via start_worker() with no hook campaigns with the
        hook registered at boot."""
        async def fake_run_worker(stop_event, ai_db=None, db=None):
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)
        monkeypatch.setattr(runtime, "_leader_hook", None)

        import database
        monkeypatch.setattr(database.db_manager, "db", mock_mongo_db)
        monkeypatch.setattr(database.db_manager, "ai_db", mock_mongo_db)

        calls = 0
        ran = asyncio.Event()

        async def hook():
            nonlocal calls
            calls += 1
            ran.set()

        assert await runtime.start_worker(on_leader_acquired=hook) == "started"
        await asyncio.wait_for(ran.wait(), timeout=5)
        assert await runtime.stop_worker() == "stopped"

        ran.clear()
        assert await runtime.start_worker() == "started"
        await asyncio.wait_for(ran.wait(), timeout=5)
        assert calls == 2
        await runtime.stop_worker()

    @pytest.mark.asyncio
    async def test_backfill_lease_blocks_concurrent_backfill(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """A backfill lease held by another process makes start_backfill()
        report already_running instead of launching a second full scan."""
        import database
        monkeypatch.setattr(database.db_manager, "db", mock_mongo_db)
        monkeypatch.setattr(database.db_manager, "ai_db", mock_mongo_db)

        assert await try_acquire_lease(
            mock_mongo_db, BACKFILL_LOCK_ID, "other-process", 60
        )
        started = asyncio.Event()

        async def fake_run_backfill(ai_db, db, stop_event=None, **_):
            started.set()
            await stop_event.wait()
            raise asyncio.CancelledError

        monkeypatch.setattr(runtime, "run_backfill", fake_run_backfill)

        assert await runtime.start_backfill() == "already_running"
        assert not runtime.is_backfill_running()
        assert await runtime.is_backfill_running_anywhere(mock_mongo_db)

        await release_lease(mock_mongo_db, BACKFILL_LOCK_ID, "other-process")
        assert await runtime.start_backfill() == "started"
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await is_lease_held(mock_mongo_db, BACKFILL_LOCK_ID)
        assert await runtime.start_backfill() == "already_running"

        assert await runtime.stop_backfill() == "stopped"
        assert not await is_lease_held(mock_mongo_db, BACKFILL_LOCK_ID)

    @pytest.mark.asyncio
    async def test_deployment_wide_stop_blocks_peer_campaign(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """stop_worker(deployment_wide=True) disables the shared switch so a
        peer's still-running campaign does not take over the lease; a
        subsequent start_worker() re-enables it."""
        async def fake_run_worker(stop_event, ai_db=None, db=None):
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)
        monkeypatch.setattr(
            type(worker_config),
            "leader_campaign_seconds",
            property(lambda self: 0.05),
        )
        monkeypatch.setattr(
            type(worker_config),
            "leader_renew_interval_seconds",
            property(lambda self: 0.05),
        )
        import database
        monkeypatch.setattr(database.db_manager, "db", mock_mongo_db)
        monkeypatch.setattr(database.db_manager, "ai_db", mock_mongo_db)

        # This process is the leader.
        assert await runtime.start_worker() == "started"
        for _ in range(100):
            if runtime.is_worker_leader():
                break
            await asyncio.sleep(0.05)
        assert runtime.is_worker_leader()

        # A peer campaign runs in "another process" (separate stop event).
        peer_stop = asyncio.Event()
        peer = asyncio.create_task(
            runtime._run_leader_campaign(peer_stop, mock_mongo_db, mock_mongo_db)
        )
        try:
            assert await runtime.stop_worker(deployment_wide=True) == "stopped"
            assert not await is_worker_enabled(mock_mongo_db)
            await asyncio.sleep(0.5)
            assert not await is_lease_held(mock_mongo_db, LEADER_LOCK_ID)

            assert await runtime.start_worker() == "started"
            assert await is_worker_enabled(mock_mongo_db)
            for _ in range(100):
                if await is_lease_held(mock_mongo_db, LEADER_LOCK_ID):
                    break
                await asyncio.sleep(0.05)
            assert await is_lease_held(mock_mongo_db, LEADER_LOCK_ID)
        finally:
            peer_stop.set()
            await asyncio.wait_for(peer, timeout=5)
            await runtime.stop_worker()

    @pytest.mark.asyncio
    async def test_start_worker_reenables_switch_even_when_locally_running(
        self, mock_mongo_db, runtime_state, monkeypatch
    ):
        """A /worker/start that lands on a peer whose (disabled) campaign is
        still alive must still flip the shared switch back on."""
        async def fake_run_worker(stop_event, ai_db=None, db=None):
            await stop_event.wait()

        monkeypatch.setattr(runtime, "run_worker", fake_run_worker)
        import database
        monkeypatch.setattr(database.db_manager, "db", mock_mongo_db)
        monkeypatch.setattr(database.db_manager, "ai_db", mock_mongo_db)

        assert await runtime.start_worker() == "started"
        try:
            await set_worker_enabled(mock_mongo_db, False)
            assert await runtime.start_worker() == "already_running"
            assert await is_worker_enabled(mock_mongo_db)
        finally:
            await runtime.stop_worker()
