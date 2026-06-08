import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from bson import ObjectId
from datetime import datetime
from typing import List, Dict, Any

from config import settings
from database import db_manager, get_db
from target_db import target_db
from auth import get_current_user
from models import (
    DashboardCreate, 
    DashboardResponse, 
    SQLQueryRequest, 
    DrillDownRequest,
    DashboardFilters
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown database connections lifespan events."""
    try:
        # Establish connection to application metadata database
        db_manager.connect()
        # Create database indexes
        await db_manager.init_indexes()
        # Seed a first claims dashboard for fresh environments
        await _seed_default_dashboards()
    except Exception as e:
        logger.critical(f"Database Initialization Failed during startup: {e}")
        # Fail loudly to prevent running app in unconfigured state
        raise e
    
    yield
    
    # Clean disconnect on shutdown
    db_manager.disconnect()

app = FastAPI(
    title="RecoveryHub Dashboard Portal API",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for React frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to FQDN domain of container app
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def serialize_mongo_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to convert MongoDB ObjectId to JSON-serializable string identifier."""
    if not doc:
        return {}
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
        doc["id"] = doc["_id"]
    return doc

def _build_default_claims_dashboard() -> Dict[str, Any]:
    """Builds the first claims dashboard template for a current-year claim flow overview.

    Widget categories
    -----------------
    * **YTD stat cards** – always scoped Jan-1-to-now; department / processor
      filters are auto-injected by the backend.
    * **Date-filtered charts** – honour the filter-bar date range via explicit
      ``%(start_date)s`` / ``%(end_date)s`` parameters (NULL-safe so they
      degrade gracefully when no dates are set).
    * **Period comparison** – compares the selected date range to an
      equally-long prior period.
    * **Monthly trend** – always YTD for context.
    """
    current_year = "DATEFROMPARTS(YEAR(GETDATE()), 1, 1)"

    # NULL-safe date filter clause fragment (reused across date-filtered widgets)
    _date_filter = (
        "(%(start_date)s IS NULL OR c.DateCreated >= %(start_date)s)\n"
        "                  AND (%(end_date)s IS NULL OR c.DateCreated <= %(end_date)s)"
    )

    return {
        "name": "Claims Calendar-Year Overview",
        "description": (
            "Year-to-date claims dashboard with dynamic date-range filtering "
            "and automatic prior-period comparison."
        ),
        "widgets": [
            # ── Row 1: YTD stat cards ────────────────────────────────
            {
                "id": "claims-draft-intake-ytd",
                "title": "Drafts Created YTD",
                "type": "stat",
                "sql_query": f"""
                WITH draft AS (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY ClaimID ORDER BY ClaimID) AS rn
                    FROM Claims FOR SYSTEM_TIME ALL
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND DateCreated >= {current_year}
                )
                SELECT COUNT(*) AS Count
                FROM draft
                WHERE rn = 1
                """,
                "layout": {"x": 0, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#6366f1"]},
            },
            {
                "id": "claims-draft-deleted-ytd",
                "title": "Deleted Drafts YTD",
                "type": "stat",
                "sql_query": f"""
                WITH DraftRoots AS (
                    SELECT DISTINCT ClaimID
                    FROM Claims FOR SYSTEM_TIME ALL
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND DateCreated >= {current_year}
                ),
                CurrentClaims AS (
                    SELECT DISTINCT ClaimID
                    FROM Claims
                )
                SELECT COUNT(*) AS Count
                FROM DraftRoots d
                LEFT JOIN CurrentClaims c ON c.ClaimID = d.ClaimID
                WHERE c.ClaimID IS NULL
                """,
                "layout": {"x": 3, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#ef4444"]},
            },
            {
                "id": "claims-draft-submitted-ytd",
                "title": "Drafts Submitted YTD",
                "type": "stat",
                "sql_query": f"""
                SELECT COUNT(DISTINCT c.original_run_id) AS Count
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.original_run_id IS NOT NULL
                  AND c.DateCreated >= {current_year}
                """,
                "layout": {"x": 6, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#22c55e"]},
            },
            {
                "id": "claims-draft-open",
                "title": "Drafts Still Open",
                "type": "stat",
                "sql_query": """
                SELECT COUNT(DISTINCT c.ClaimID) AS Count
                FROM Claims c
                WHERE c.submitted = 0
                  AND c.original_run_id IS NULL
                """,
                "layout": {"x": 9, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#f59e0b"]},
            },

            # ── Row 2: date-filtered status charts ───────────────────
            {
                "id": "claims-new-runs-by-status",
                "title": "New Runs by Status",
                "type": "bar",
                "sql_query": f"""
                SELECT c.Status, COUNT(DISTINCT c.ClaimID) AS Count
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.original_run_id IS NOT NULL
                  AND {_date_filter}
                GROUP BY c.Status
                ORDER BY Count DESC, c.Status
                """,
                "layout": {"x": 0, "y": 3, "w": 6, "h": 5},
                "config": {"xAxisKey": "Status", "yAxisKeys": ["Count"], "colors": ["#8b5cf6"]},
            },
            {
                "id": "claims-active-by-status",
                "title": "Active Runs by Status",
                "type": "bar",
                "sql_query": f"""
                SELECT c.Status, COUNT(DISTINCT c.ClaimID) AS Count
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.original_run_id IS NOT NULL
                  AND c.user_id <> '0'
                  AND c.Status <> 'Unassigned'
                  AND {_date_filter}
                GROUP BY c.Status
                ORDER BY Count DESC, c.Status
                """,
                "layout": {"x": 6, "y": 3, "w": 6, "h": 5},
                "config": {"xAxisKey": "Status", "yAxisKeys": ["Count"], "colors": ["#0ea5e9"]},
            },

            # ── Row 3: financial YTD stat cards ──────────────────────
            {
                "id": "claims-total-amount-ytd",
                "title": "Total Claims Amount YTD",
                "type": "stat",
                "sql_query": f"""
                SELECT COALESCE(SUM(c.Amount), 0) AS Amount
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.DateCreated >= {current_year}
                """,
                "layout": {"x": 0, "y": 8, "w": 4, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#14b8a6"], "format": "currency"},
            },
            {
                "id": "claims-avg-amount",
                "title": "Avg Claim Amount (Period)",
                "type": "stat",
                "sql_query": f"""
                SELECT COALESCE(AVG(c.Amount), 0) AS Amount
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND {_date_filter}
                """,
                "layout": {"x": 4, "y": 8, "w": 4, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#0ea5e9"], "format": "currency"},
            },
            {
                "id": "claims-amount-by-status",
                "title": "Amount by Status (Period)",
                "type": "bar",
                "sql_query": f"""
                SELECT c.Status, COALESCE(SUM(c.Amount), 0) AS Amount
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND {_date_filter}
                GROUP BY c.Status
                ORDER BY Amount DESC
                """,
                "layout": {"x": 8, "y": 8, "w": 4, "h": 3},
                "config": {"xAxisKey": "Status", "yAxisKeys": ["Amount"], "colors": ["#f97316"], "format": "currency"},
            },

            # ── Row 4: period comparison + monthly trend ─────────────
            {
                "id": "claims-period-comparison",
                "title": "Drafts Created – Selected vs Prior Period",
                "type": "table",
                "sql_query": """
                WITH selected_draft AS (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY ClaimID ORDER BY ClaimID) AS rn
                    FROM Claims FOR SYSTEM_TIME BETWEEN %(start_date)s AND %(end_date)s
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND DateCreated BETWEEN %(start_date)s AND %(end_date)s
                ),
                prior_draft AS (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY ClaimID ORDER BY ClaimID) AS rn
                    FROM Claims FOR SYSTEM_TIME BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND DateCreated BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                )
                SELECT 'Selected Period' AS Period, COUNT(*) AS DraftsCreated
                FROM selected_draft WHERE rn = 1
                UNION ALL
                SELECT 'Prior Period' AS Period, COUNT(*) AS DraftsCreated
                FROM prior_draft WHERE rn = 1
                """,
                "layout": {"x": 0, "y": 11, "w": 6, "h": 4},
                "config": {"xAxisKey": "Period", "yAxisKeys": ["DraftsCreated"], "colors": ["#14b8a6"]},
            },
            {
                "id": "claims-submitted-period-comparison",
                "title": "Drafts Submitted During This Period",
                "type": "table",
                "sql_query": """
                WITH selected_submitted AS (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY ClaimID ORDER BY ClaimID) AS rn
                    FROM Claims FOR SYSTEM_TIME BETWEEN %(start_date)s AND %(end_date)s
                    WHERE submitted = 1
                      AND archived = 0
                      AND original_run_id IS NOT NULL
                      AND DateSubmitted BETWEEN %(start_date)s AND %(end_date)s
                ),
                prior_submitted AS (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY ClaimID ORDER BY ClaimID) AS rn
                    FROM Claims FOR SYSTEM_TIME BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                    WHERE submitted = 1
                      AND archived = 0
                      AND original_run_id IS NOT NULL
                      AND DateSubmitted BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                )
                SELECT 'Selected Period' AS Period, COUNT(*) AS DraftsSubmitted
                FROM selected_submitted WHERE rn = 1
                UNION ALL
                SELECT 'Prior Period' AS Period, COUNT(*) AS DraftsSubmitted
                FROM prior_submitted WHERE rn = 1
                """,
                "layout": {"x": 6, "y": 11, "w": 6, "h": 4},
                "config": {"xAxisKey": "Period", "yAxisKeys": ["DraftsSubmitted"], "colors": ["#22c55e"]},
            },

            # ── Row 5: monthly trend ──────────────────────────────────
            {
                "id": "claims-monthly-trend",
                "title": "Monthly Claims Trend (YTD)",
                "type": "line",
                "sql_query": f"""
                SELECT
                    FORMAT(c.DateCreated, 'MMM') AS Month,
                    COUNT(DISTINCT c.ClaimID) AS Claims
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.DateCreated >= {current_year}
                GROUP BY FORMAT(c.DateCreated, 'MMM'), MONTH(c.DateCreated)
                ORDER BY MONTH(c.DateCreated)
                """,
                "layout": {"x": 0, "y": 15, "w": 12, "h": 4},
                "config": {"xAxisKey": "Month", "yAxisKeys": ["Claims"], "colors": ["#6366f1"]},
            },
        ],
    }


async def _seed_default_dashboards() -> None:
    """Creates the first claims dashboard when no dashboards exist yet."""
    if os.getenv("TESTING") == "true":
        return

    dashboards = db_manager.db["dashboards"]
    existing_count = await dashboards.count_documents({})
    if existing_count > 0:
        return

    now = datetime.utcnow()
    payload = _build_default_claims_dashboard()
    payload["created_by"] = "system"
    payload["created_at"] = now
    payload["updated_at"] = now

    await dashboards.insert_one(payload)
    logger.info("Seeded default claims dashboard: Claims Calendar-Year Overview")

# --- DASHBOARD METADATA ENDPOINTS ---

@app.get(
    "/api/dashboards", 
    response_model=List[DashboardResponse],
    dependencies=[Depends(get_current_user)]
)
async def list_dashboards(db = Depends(get_db)):
    """Retrieves all saved dashboard layout configurations from MongoDB."""
    cursor = db["dashboards"].find().sort("created_at", -1)
    dashboards = await cursor.to_list(length=100)
    return [serialize_mongo_doc(dash) for dash in dashboards]

@app.get(
    "/api/dashboards/{dashboard_id}", 
    response_model=DashboardResponse,
    dependencies=[Depends(get_current_user)]
)
async def get_dashboard(dashboard_id: str, db = Depends(get_db)):
    """Fetches a specific dashboard layout by ID."""
    if not ObjectId.is_valid(dashboard_id):
        raise HTTPException(status_code=400, detail="Invalid dashboard ID format.")
    
    dash = await db["dashboards"].find_one({"_id": ObjectId(dashboard_id)})
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    
    return serialize_mongo_doc(dash)

@app.post(
    "/api/dashboards", 
    response_model=DashboardResponse
)
async def create_dashboard(
    dashboard: DashboardCreate, 
    db = Depends(get_db), 
    user: dict = Depends(get_current_user)
):
    """Saves a new dashboard configuration to MongoDB."""
    doc = dashboard.model_dump()
    
    # Inject metadata
    doc["created_by"] = user.get("preferred_username") or user.get("upn") or "anonymous"
    doc["created_at"] = datetime.utcnow()
    doc["updated_at"] = datetime.utcnow()
    
    result = await db["dashboards"].insert_one(doc)
    doc["_id"] = result.inserted_id
    
    return serialize_mongo_doc(doc)

@app.put(
    "/api/dashboards/{dashboard_id}", 
    response_model=DashboardResponse
)
async def update_dashboard(
    dashboard_id: str, 
    dashboard: DashboardCreate, 
    db = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    """Updates properties or widget layouts of an existing dashboard."""
    if not ObjectId.is_valid(dashboard_id):
        raise HTTPException(status_code=400, detail="Invalid dashboard ID format.")
        
    doc = dashboard.model_dump()
    doc["updated_at"] = datetime.utcnow()
    
    result = await db["dashboards"].find_one_and_update(
        {"_id": ObjectId(dashboard_id)},
        {"$set": doc},
        return_document=True
    )
    
    if not result:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
        
    return serialize_mongo_doc(result)

@app.delete(
    "/api/dashboards/{dashboard_id}",
    dependencies=[Depends(get_current_user)]
)
async def delete_dashboard(dashboard_id: str, db = Depends(get_db)):
    """Removes a dashboard page from the metadata store."""
    if not ObjectId.is_valid(dashboard_id):
        raise HTTPException(status_code=400, detail="Invalid dashboard ID format.")
        
    result = await db["dashboards"].delete_one({"_id": ObjectId(dashboard_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
        
    return {"success": True}

# --- AZURE SQL EXECUTION ENDPOINTS ---

@app.post(
    "/api/query/sql",
    dependencies=[Depends(get_current_user)]
)
async def run_query(request: SQLQueryRequest):
    """Runs a read-only parameterized query against the target database.
    
    Throws 400 Bad Request with driver stack details on compilation or safety failures.
    """
    try:
        result = target_db.execute_read(request.sql_query, request.filters)
        return result
    except Exception as e:
        logger.error(f"SQL execution request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@app.get(
    "/api/schema/sql",
    dependencies=[Depends(get_current_user)]
)
async def get_schema():
    """Returns database structures and metadata tables representing Azure SQL tables."""
    try:
        return target_db.get_db_schema()
    except Exception as e:
        logger.error(f"Schema load request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get(
    "/api/filters/options",
    dependencies=[Depends(get_current_user)]
)
async def get_filters():
    """Loads active filter options from database tables."""
    try:
        return target_db.get_filter_dropdown_options()
    except Exception as e:
        logger.error(f"Filter options query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.post(
    "/api/query/drilldown",
    dependencies=[Depends(get_current_user)]
)
async def run_drilldown(request: DrillDownRequest):
    """Executes a secure claims search mapping clicked visualization elements."""
    try:
        return target_db.execute_drilldown(
            field_name=request.field_name,
            field_value=request.field_value,
            filters=request.filters
        )
    except Exception as e:
        logger.error(f"Drilldown SQL query execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
