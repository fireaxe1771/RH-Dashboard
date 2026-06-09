"""Tests for billing.scheduler job registration and job wrappers."""
import pytest

from billing import scheduler


@pytest.fixture(autouse=True)
def _clean_scheduler():
    for job in list(scheduler.billing_scheduler.get_jobs()):
        scheduler.billing_scheduler.remove_job(job.id)
    yield
    for job in list(scheduler.billing_scheduler.get_jobs()):
        scheduler.billing_scheduler.remove_job(job.id)


def test_setup_registers_all_jobs():
    scheduler.setup_billing_jobs()
    ids = {job.id for job in scheduler.billing_scheduler.get_jobs()}
    assert ids == {
        "billing_daily_sync",
        "billing_advisor_sync",
        "billing_reservation_sync",
        "billing_invoice_sync",
        "billing_resource_inventory",
    }


def test_setup_uses_replace_existing_ids():
    # All jobs are registered with replace_existing=True so the same ids are reused
    scheduler.setup_billing_jobs()
    ids = [job.id for job in scheduler.billing_scheduler.get_jobs()]
    assert len(set(ids)) == 5


@pytest.mark.asyncio
async def test_daily_sync_job_skips_when_no_db(monkeypatch):
    monkeypatch.setattr(scheduler.db_manager, "db", None)
    # Should not raise even though DB is unavailable
    await scheduler._daily_sync_job()


@pytest.mark.asyncio
async def test_daily_sync_job_runs_sync_then_vectorize(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    calls = []

    async def fake_daily(db):
        calls.append("sync")
        return {"ok": True}

    async def fake_vec(db):
        calls.append("vectorize")
        return 0

    monkeypatch.setattr(scheduler.sync_service, "run_daily_sync", fake_daily)
    monkeypatch.setattr(scheduler.vectorizer, "run_vectorization", fake_vec)

    await scheduler._daily_sync_job()
    assert calls == ["sync", "vectorize"]


@pytest.mark.asyncio
async def test_daily_sync_job_swallows_errors(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)

    async def boom(db):
        raise RuntimeError("fail")

    monkeypatch.setattr(scheduler.sync_service, "run_daily_sync", boom)
    # Must not propagate
    await scheduler._daily_sync_job()
