"""One-off: run the billing full backfill directly against Atlas.

Uses the same sync_service the backend scheduler runs. Env vars for the
billing service principal are injected by the caller (never written to .env).
"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from database import db_manager  # noqa: E402
from billing import sync_service  # noqa: E402
from config import settings  # noqa: E402


async def main() -> int:
    db_manager.connect()
    try:
        db = db_manager.db
        if db is None:
            print("ERROR: db_manager.db is None after connect")
            return 1
        result = await sync_service.run_full_backfill(db, settings.BILLING_HISTORY_MONTHS, "manual_local")
        print(f"BACKFILL RESULT: {result}")
        return 0
    finally:
        if db_manager.client:
            db_manager.client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
