"""APScheduler AsyncIOScheduler configuration and billing sync job definitions."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import settings
from database import db_manager
from billing import sync_service, vectorizer

logger = logging.getLogger(__name__)

billing_scheduler = AsyncIOScheduler(timezone="UTC")


def setup_billing_jobs() -> None:
    """Registers all billing sync jobs on the scheduler."""

    # Daily cost + budget sync at configured hour (default 2:00 AM UTC)
    billing_scheduler.add_job(
        _daily_sync_job,
        CronTrigger(hour=settings.BILLING_DAILY_SYNC_HOUR, minute=0),
        id="billing_daily_sync",
        name="Azure Billing Daily Sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Advisor recommendations — every Monday at 3:00 AM UTC
    billing_scheduler.add_job(
        _advisor_sync_job,
        CronTrigger(day_of_week="mon", hour=3, minute=0),
        id="billing_advisor_sync",
        name="Azure Advisor Recommendations Sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Reservations — every Monday at 3:30 AM UTC
    billing_scheduler.add_job(
        _reservation_sync_job,
        CronTrigger(day_of_week="mon", hour=3, minute=30),
        id="billing_reservation_sync",
        name="Azure Reservation Sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Invoices — 5th of each month at 4:00 AM UTC
    billing_scheduler.add_job(
        _invoice_sync_job,
        CronTrigger(day=5, hour=4, minute=0),
        id="billing_invoice_sync",
        name="Azure Invoice Sync",
        replace_existing=True,
        misfire_grace_time=86400,
    )

    # Resource inventory — every Sunday at 1:00 AM UTC
    billing_scheduler.add_job(
        _resource_inventory_job,
        CronTrigger(day_of_week="sun", hour=1, minute=0),
        id="billing_resource_inventory",
        name="Azure Resource Inventory Sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )


async def _daily_sync_job() -> None:
    """Scheduled wrapper for daily billing sync."""
    db = db_manager.db
    if db is None:
        logger.error("Scheduler: DB not available, skipping daily sync.")
        return
    try:
        result = await sync_service.run_daily_sync(db)
        logger.info(f"Daily billing sync completed: {result}")
        # Trigger vectorization after successful sync
        await vectorizer.run_vectorization(db)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Daily billing sync failed: {e}")


async def _advisor_sync_job() -> None:
    db = db_manager.db
    if db is None:
        logger.error("Scheduler: DB not available, skipping advisor sync.")
        return
    try:
        count = await sync_service.sync_advisor_recommendations(db, "scheduler")
        logger.info(f"Advisor sync completed: {count} recommendations.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Advisor sync failed: {e}")


async def _reservation_sync_job() -> None:
    db = db_manager.db
    if db is None:
        logger.error("Scheduler: DB not available, skipping reservation sync.")
        return
    try:
        count = await sync_service.sync_reservations(db, "scheduler")
        logger.info(f"Reservation sync completed: {count} records.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Reservation sync failed: {e}")


async def _invoice_sync_job() -> None:
    db = db_manager.db
    if db is None:
        logger.error("Scheduler: DB not available, skipping invoice sync.")
        return
    try:
        count = await sync_service.sync_invoices(db, "scheduler")
        logger.info(f"Invoice sync completed: {count} invoices.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Invoice sync failed: {e}")


async def _resource_inventory_job() -> None:
    db = db_manager.db
    if db is None:
        logger.error("Scheduler: DB not available, skipping resource inventory sync.")
        return
    try:
        count = await sync_service.sync_resource_inventory(db, "scheduler")
        logger.info(f"Resource inventory sync completed: {count} resources.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Resource inventory sync failed: {e}")
