import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config import settings

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages asynchronous database connection pooling for MongoDB app metadata."""

    def __init__(self):
        self.client: AsyncIOMotorClient = None
        self.db = None

    def connect(self) -> None:
        """Establishes connection client to the Azure/local MongoDB instance."""
        try:
            logger.info("Initializing asynchronous connection pool to MongoDB...")
            self.client = AsyncIOMotorClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=5000  # Timeout quickly if unreachable (fail loudly)
            )
            self.db = self.client[settings.MONGODB_DB_NAME]
            logger.info(f"Successfully bound to database: '{settings.MONGODB_DB_NAME}'")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB instance: {e}")
            raise e

    def disconnect(self) -> None:
        """Closes MongoDB client sessions."""
        if self.client:
            logger.info("Closing MongoDB connection client...")
            self.client.close()
            logger.info("MongoDB connection closed.")

    async def init_indexes(self) -> None:
        """Pre-configures collection indexes on startup to ensure high lookup speeds."""
        if self.db is None:
            raise RuntimeError("Database connection not established. Call connect() first.")
        
        try:
            # Index dashboards by owner and creation date
            await self.db["dashboards"].create_index([("created_by", 1)])
            # Unique constraint on users email
            await self.db["users"].create_index([("email", 1)], unique=True)

            # --- Azure billing collection indexes ---
            await self.db["azure_cost_details"].create_index([("billing_period", 1), ("subscription_id", 1)])
            await self.db["azure_cost_details"].create_index([("date", 1)])
            await self.db["azure_cost_details"].create_index([("service_name", 1), ("billing_period", 1)])
            await self.db["azure_cost_details"].create_index([("resource_group", 1), ("billing_period", 1)])
            await self.db["azure_cost_details"].create_index([("charge_type", 1)])
            await self.db["azure_cost_details"].create_index([("pre_tax_cost", -1)])

            await self.db["azure_cost_summary"].create_index(
                [("period", 1), ("dimension", 1), ("subscription_id", 1)],
                unique=True
            )
            await self.db["azure_cost_summary"].create_index([("period", 1), ("total_cost", -1)])
            await self.db["azure_cost_summary"].create_index([("dimension", 1)])

            await self.db["azure_invoices"].create_index([("invoice_id", 1)], unique=True)
            await self.db["azure_invoices"].create_index([("billing_period_start", -1)])
            await self.db["azure_invoices"].create_index([("status", 1)])

            await self.db["azure_budgets"].create_index([("scope", 1)])
            await self.db["azure_budgets"].create_index([("utilization_pct", -1)])
            await self.db["azure_budgets"].create_index([("time_grain", 1)])

            await self.db["azure_cost_alerts"].create_index([("status", 1)])
            await self.db["azure_cost_alerts"].create_index([("creation_time", -1)])
            await self.db["azure_cost_alerts"].create_index([("alert_type", 1)])

            await self.db["azure_advisor_recommendations"].create_index([("category", 1), ("status", 1)])
            await self.db["azure_advisor_recommendations"].create_index([("estimated_monthly_savings", -1)])
            await self.db["azure_advisor_recommendations"].create_index([("subscription_id", 1)])
            await self.db["azure_advisor_recommendations"].create_index([("impact", 1)])
            await self.db["azure_advisor_recommendations"].create_index([("last_updated", -1)])

            await self.db["azure_reservation_details"].create_index([("billing_period", 1)])
            await self.db["azure_reservation_details"].create_index([("reservation_id", 1), ("usage_date", 1)], unique=True)
            await self.db["azure_reservation_details"].create_index([("utilization_pct", 1)])

            await self.db["azure_reservation_recommendations"].create_index([("net_savings", -1)])
            await self.db["azure_reservation_recommendations"].create_index([("term", 1)])
            await self.db["azure_reservation_recommendations"].create_index([("subscription_id", 1)])

            await self.db["azure_resource_inventory"].create_index([("resource_id", 1)], unique=True)
            await self.db["azure_resource_inventory"].create_index([("resource_type", 1)])
            await self.db["azure_resource_inventory"].create_index([("resource_group", 1)])
            await self.db["azure_resource_inventory"].create_index([("subscription_id", 1)])

            await self.db["azure_retail_prices"].create_index([("meter_id", 1), ("arm_region_name", 1)], unique=False)
            await self.db["azure_retail_prices"].create_index([("service_name", 1)])
            await self.db["azure_retail_prices"].create_index([("sku_name", 1)])

            await self.db["azure_billing_sync_log"].create_index([("sync_type", 1), ("started_at", -1)])
            await self.db["azure_billing_sync_log"].create_index([("status", 1)])
            await self.db["azure_billing_sync_log"].create_index([("started_at", -1)])

            await self.db["azure_billing_vectors"].create_index([("document_type", 1)])
            await self.db["azure_billing_vectors"].create_index([("metadata.period", 1)])
            await self.db["azure_billing_vectors"].create_index([("created_at", -1)])

            # Warn (do not fail) if the Atlas Vector Search index is not present
            await self._check_vector_index()

            logger.info("MongoDB index initialization completed.")
        except Exception as e:
            logger.error(f"Failed to create indexes in MongoDB: {e}")
            raise e

    async def _check_vector_index(self) -> None:
        """Checks for the Atlas Vector Search index and logs a warning if missing."""
        try:
            indexes = await self.db["azure_billing_vectors"].list_search_indexes().to_list(length=10)
            vector_index_exists = any(idx.get("name") == "billing_vector_index" for idx in indexes)
            if not vector_index_exists:
                logger.warning(
                    "Atlas Vector Search index 'billing_vector_index' is missing on "
                    "'azure_billing_vectors' collection. AI billing queries will not work until "
                    "this index is created. See docs/supporting/04-mongodb-schema-design.md "
                    "Section 12 for the index JSON definition and creation instructions."
                )
        except Exception as e:
            logger.warning(f"Could not verify Atlas Vector Search index existence: {e}")

# Single instance of DB Manager for the backend application
db_manager = DatabaseManager()

def get_db():
    """Dependency provider yielding active MongoDB instance."""
    if db_manager.db is None:
        raise RuntimeError("Database connection is offline.")
    return db_manager.db
