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
            logger.info("MongoDB index initialization completed.")
        except Exception as e:
            logger.error(f"Failed to create indexes in MongoDB: {e}")
            raise e

# Single instance of DB Manager for the backend application
db_manager = DatabaseManager()

def get_db():
    """Dependency provider yielding active MongoDB instance."""
    if db_manager.db is None:
        raise RuntimeError("Database connection is offline.")
    return db_manager.db
