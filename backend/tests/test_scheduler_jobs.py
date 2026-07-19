"""Tests for billing.scheduler job wrapper functions (advisor, reservation, invoice, resource_inventory)."""
import pytest
from unittest.mock import AsyncMock

from billing import scheduler


@pytest.mark.asyncio
async def test_advisor_sync_job_skips_when_no_db(monkeypatch):
    monkeypatch.setattr(scheduler.db_manager, "db", None)
    await scheduler._advisor_sync_job()


@pytest.mark.asyncio
async def test_advisor_sync_job_runs_sync(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_advisor_recommendations", AsyncMock(return_value=5))
    await scheduler._advisor_sync_job()
    scheduler.sync_service.sync_advisor_recommendations.assert_called_once_with(mock_mongo_db, "scheduler")


@pytest.mark.asyncio
async def test_advisor_sync_job_swallows_errors(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_advisor_recommendations", AsyncMock(side_effect=RuntimeError("fail")))
    await scheduler._advisor_sync_job()


@pytest.mark.asyncio
async def test_reservation_sync_job_skips_when_no_db(monkeypatch):
    monkeypatch.setattr(scheduler.db_manager, "db", None)
    await scheduler._reservation_sync_job()


@pytest.mark.asyncio
async def test_reservation_sync_job_runs_sync(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_reservations", AsyncMock(return_value=3))
    await scheduler._reservation_sync_job()
    scheduler.sync_service.sync_reservations.assert_called_once_with(mock_mongo_db, "scheduler")


@pytest.mark.asyncio
async def test_reservation_sync_job_swallows_errors(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_reservations", AsyncMock(side_effect=RuntimeError("fail")))
    await scheduler._reservation_sync_job()


@pytest.mark.asyncio
async def test_invoice_sync_job_skips_when_no_db(monkeypatch):
    monkeypatch.setattr(scheduler.db_manager, "db", None)
    await scheduler._invoice_sync_job()


@pytest.mark.asyncio
async def test_invoice_sync_job_runs_sync(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_invoices", AsyncMock(return_value=2))
    await scheduler._invoice_sync_job()
    scheduler.sync_service.sync_invoices.assert_called_once_with(mock_mongo_db, "scheduler")


@pytest.mark.asyncio
async def test_invoice_sync_job_swallows_errors(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_invoices", AsyncMock(side_effect=RuntimeError("fail")))
    await scheduler._invoice_sync_job()


@pytest.mark.asyncio
async def test_resource_inventory_job_skips_when_no_db(monkeypatch):
    monkeypatch.setattr(scheduler.db_manager, "db", None)
    await scheduler._resource_inventory_job()


@pytest.mark.asyncio
async def test_resource_inventory_job_runs_sync(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_resource_inventory", AsyncMock(return_value=10))
    await scheduler._resource_inventory_job()
    scheduler.sync_service.sync_resource_inventory.assert_called_once_with(mock_mongo_db, "scheduler")


@pytest.mark.asyncio
async def test_resource_inventory_job_swallows_errors(monkeypatch, mock_mongo_db):
    monkeypatch.setattr(scheduler.db_manager, "db", mock_mongo_db)
    monkeypatch.setattr(scheduler.sync_service, "sync_resource_inventory", AsyncMock(side_effect=RuntimeError("fail")))
    await scheduler._resource_inventory_job()
