"""FastAPI application entry point for the RecoveryHub Dashboard backend.

Configures CORS, mounts all route groups (claims dashboards, AI analytics,
AI adoption, billing), seeds default dashboards on startup, and manages the
APScheduler-based billing sync scheduler lifecycle (start on startup,
graceful shutdown on stop).
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from bson import ObjectId
from datetime import UTC, datetime
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
from billing.scheduler import billing_scheduler, setup_billing_jobs
from billing.sync_service import run_full_backfill
from billing_routes import billing_router
from ai_adoption_routes import ai_adoption_router
from ai_analytics_routes import ai_analytics_router
from ai_analytics_worker.config import worker_config
from ai_analytics_worker.main import run_worker, stop_worker_task

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Module-level handle for the worker task and its stop event, set during
# lifespan startup and awaited during shutdown. Kept at module scope so the
# shutdown branch can reference them without threading them through `yield`.
_worker_task: asyncio.Task | None = None
_worker_stop_event: asyncio.Event | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown database connections lifespan events."""
    global _worker_task, _worker_stop_event

    try:
        # Establish connection to application metadata database
        db_manager.connect()
        # Create database indexes
        await db_manager.init_indexes()
        # Seed a first claims dashboard for fresh environments
        await _seed_default_dashboards()
        # Start billing sync scheduler if enabled
        if settings.BILLING_SYNC_ENABLED:
            setup_billing_jobs()
            billing_scheduler.start()
            logger.info("Billing sync scheduler started.")
            # Run initial backfill in background (no-op if already populated)
            asyncio.create_task(_run_billing_backfill_if_needed())
        # Start the AI Analytics Worker if enabled. The worker runs as a
        # background asyncio task in this event loop (Phase 0 plan Section 1.1).
        # Phase 5: change-stream listener that processes events in near-real-time.
        if worker_config.enabled:
            _worker_stop_event = asyncio.Event()
            _worker_task = asyncio.create_task(
                run_worker(
                    _worker_stop_event,
                    ai_db=db_manager.ai_db,
                    db=db_manager.db,
                )
            )
            logger.info("AI Analytics Worker task started.")
    except Exception as e:
        logger.critical(f"Database Initialization Failed during startup: {e}")
        # Fail loudly to prevent running app in unconfigured state
        raise e

    yield

    # Clean disconnect on shutdown
    # Stop the AI Analytics Worker first — it must drain within
    # CANCELLATION_TIMEOUT_SECONDS (5s) per the Phase 0 plan Section 1.1.4.
    if _worker_task is not None and _worker_stop_event is not None:
        logger.info("Stopping AI Analytics Worker task...")
        await stop_worker_task(_worker_task, _worker_stop_event)
        _worker_task = None
        _worker_stop_event = None
        logger.info("AI Analytics Worker task stopped.")
    if settings.BILLING_SYNC_ENABLED and billing_scheduler.running:
        logger.info("Shutting down billing scheduler, waiting for in-flight jobs to complete...")
        billing_scheduler.shutdown(wait=True)
        logger.info("Billing sync scheduler stopped cleanly.")
    db_manager.disconnect()


async def _run_billing_backfill_if_needed() -> None:
    """Checks if billing data exists; runs full backfill if not. Background task."""
    try:
        db = db_manager.db
        count = await db["azure_cost_details"].count_documents({})
        if count == 0:
            logger.info("No billing data found. Starting historical backfill...")
            await run_full_backfill(db, settings.BILLING_HISTORY_MONTHS, "startup_backfill")
    except Exception as e:
        logger.error(f"Billing backfill check failed: {e}")

app = FastAPI(
    title="RecoveryHub Dashboard Portal API",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for React frontend requests.
# Restricted to the configured frontend origin (FRONTEND_URL) to avoid the
# CSRF risk of allow_origins=["*"] combined with allow_credentials=True.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the Azure billing analytics router
app.include_router(billing_router, prefix="/api/billing")
app.include_router(ai_adoption_router, prefix="/api/ai-adoption")
app.include_router(ai_analytics_router, prefix="/api")

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
    # NULL-safe date filter clause fragment (reused across date-filtered widgets)
    _date_filter = (
        "(%(start_date)s IS NULL OR c.created >= %(start_date)s)\n"
        "                  AND (%(end_date)s IS NULL OR c.created <= %(end_date)s)"
    )

    return {
        "name": "Claims Breakdown",
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
                SELECT
                    FORMAT(COUNT(*), '#,0') + ' / '
                    + FORMAT(CAST(ROUND(COUNT(*) * 1.0 / DATEPART(WEEK, CAST(%(end_date)s AS DATE)), 0) AS INT), '#,0')
                    + ' per week' AS Count
                FROM (
                    SELECT id FROM Claims
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND created BETWEEN %(ytd_start)s AND %(end_date)s
                    UNION
                    SELECT id FROM Claims
                    WHERE submitted = 1
                      AND original_run_id IS NOT NULL
                      AND created BETWEEN %(ytd_start)s AND %(end_date)s
                    UNION
                    SELECT id FROM dbo.claims_deleted
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND created BETWEEN %(ytd_start)s AND %(end_date)s
                ) AS draft
                """,
                "layout": {"x": 0, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#6366f1"]},
            },
            {
                "id": "claims-draft-deleted-ytd",
                "title": "Deleted Drafts YTD",
                "type": "stat",
                "sql_query": f"""
                SELECT
                    FORMAT(COUNT(DISTINCT id), '#,0') + ' / '
                    + FORMAT(CAST(ROUND(COUNT(DISTINCT id) * 1.0 / DATEPART(WEEK, CAST(%(end_date)s AS DATE)), 0) AS INT), '#,0')
                    + ' per week' AS Count
                FROM dbo.claims_deleted
                WHERE submitted = 0
                  AND original_run_id IS NULL
                  AND created BETWEEN %(ytd_start)s AND %(end_date)s
                  -- `timestamp` captures the deletion event (set when the
                  -- row is moved into claims_deleted, then immutable).
                  -- Require both creation AND deletion to fall in YTD so
                  -- the metric mirrors Drafts Created YTD over the same
                  -- window while only counting drafts actually deleted
                  -- during that window.
                  AND [timestamp] BETWEEN %(ytd_start)s AND %(end_date)s
                """,
                "layout": {"x": 3, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#ef4444"]},
            },
            {
                "id": "claims-draft-submitted-ytd",
                "title": "Drafts Submitted YTD",
                "type": "stat",
                "sql_query": f"""
                SELECT
                    FORMAT(COUNT(DISTINCT c.original_run_id), '#,0') + ' / '
                    + FORMAT(CAST(ROUND(COUNT(DISTINCT c.original_run_id) * 1.0 / DATEPART(WEEK, CAST(%(end_date)s AS DATE)), 0) AS INT), '#,0')
                    + ' per week' AS Count
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.original_run_id IS NOT NULL
                  AND c.date_of_submitted >= %(ytd_start)s
                  AND c.date_of_submitted <= %(end_date)s
                """,
                "layout": {"x": 6, "y": 0, "w": 3, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#22c55e"]},
            },

            # ── Row 2: combined current-status stat card ───────────
            # Fuses the former "Drafts Still Open", "Current New Runs", and
            # "Current Active Runs" tiles into a single 3-value stat card.
            # Each subquery is the exact filter from the old standalone widget,
            # returned as its own column so the stat card can render them side
            # by side with per-value labels.
            {
                "id": "claims-current-summary",
                "title": "Current Claims Summary",
                "type": "stat",
                "sql_query": f"""
                SELECT
                    (SELECT COUNT(DISTINCT c.id)
                     FROM Claims c
                     WHERE c.submitted = 0
                       AND c.original_run_id IS NULL
                       AND c.created <= %(end_date)s) AS Drafts,
                    (SELECT COUNT(DISTINCT c.id)
                     FROM Claims c
                     WHERE c.submitted = 1
                       AND c.archived = 0
                       AND c.original_run_id IS NOT NULL
                       AND c.ClaimCurrentTypeId = 1) AS NewRuns,
                    (SELECT COUNT(DISTINCT c.id)
                     FROM Claims c
                     WHERE c.submitted = 1
                       AND c.archived = 0
                       AND c.original_run_id IS NOT NULL
                       AND c.ClaimCurrentTypeId = 4) AS ActiveRuns
                """,
                "layout": {"x": 0, "y": 3, "w": 6, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#f59e0b", "#8b5cf6", "#0ea5e9"]},
            },

            # ── Row 3: temporal status charts (runs processed during period) ──
            {
                "id": "claims-new-runs-by-type",
                "title": "New Runs – Submitted vs Recycled",
                "type": "bar",
                "sql_query": """
                SELECT 'Submitted' AS RunType, COUNT(*) AS Count
                FROM Claims
                WHERE submitted = 1
                  AND original_run_id IS NULL
                  AND date_of_submitted BETWEEN %(start_date)s AND %(end_date)s
                UNION ALL
                SELECT 'Recycled' AS RunType, COUNT(*) AS Count
                FROM Claims
                WHERE submitted = 0
                  AND original_run_id IS NULL
                  AND date_of_submitted BETWEEN %(start_date)s AND %(end_date)s
                """,
                "layout": {"x": 6, "y": 3, "w": 6, "h": 3},
                "config": {"xAxisKey": "RunType", "yAxisKeys": ["Count"], "colors": ["#8b5cf6"]},
            },
            {
                "id": "claims-active-by-status",
                "title": "Active Runs Processed During Period",
                "type": "bar",
                "sql_query": """
                SELECT status, COUNT(*) AS Count
                FROM Claims
                WHERE ClaimCurrentTypeId = 4
                  AND submitted = 1
                  AND archived = 0
                  AND original_run_id IS NOT NULL
                  AND date_of_submitted BETWEEN %(start_date)s AND %(end_date)s
                GROUP BY status
                ORDER BY Count DESC, status
                """,
                "layout": {"x": 0, "y": 6, "w": 12, "h": 5},
                "config": {"xAxisKey": "status", "yAxisKeys": ["Count"], "colors": ["#0ea5e9"]},
            },

            # ── Row 4: financial YTD stat cards ──────────────────────
            {
                "id": "claims-total-amount-ytd",
                "title": "Total Claims Amount YTD",
                "type": "stat",
                "sql_query": f"""
                SELECT COALESCE(SUM(c.amount_invoiced), 0) AS Amount
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.created >= %(ytd_start)s
                  AND c.created <= %(end_date)s
                """,
                "layout": {"x": 0, "y": 11, "w": 4, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#14b8a6"], "format": "currency"},
            },
            {
                "id": "claims-avg-amount",
                "title": "Avg Claim Amount (Period)",
                "type": "stat",
                "sql_query": f"""
                SELECT COALESCE(AVG(c.amount_invoiced), 0) AS Amount
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND {_date_filter}
                """,
                "layout": {"x": 4, "y": 11, "w": 4, "h": 3},
                "config": {"xAxisKey": "", "yAxisKeys": [], "colors": ["#0ea5e9"], "format": "currency"},
            },
            {
                "id": "claims-amount-by-status",
                "title": "Amount by Status (Period)",
                "type": "bar",
                "sql_query": f"""
                SELECT c.status, COALESCE(SUM(c.amount_invoiced), 0) AS Amount
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND {_date_filter}
                GROUP BY c.status
                ORDER BY Amount DESC
                """,
                "layout": {"x": 8, "y": 11, "w": 4, "h": 3},
                "config": {"xAxisKey": "status", "yAxisKeys": ["Amount"], "colors": ["#f97316"], "format": "currency"},
            },

            # ── Row 5: period comparison + monthly trend ─────────────
            {
                "id": "claims-period-comparison",
                "title": "Drafts Created – Selected vs Prior Period",
                "type": "table",
                "sql_query": """
                WITH selected_draft AS (
                    SELECT id FROM Claims
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND created BETWEEN %(start_date)s AND %(end_date)s
                    UNION
                    SELECT id FROM Claims
                    WHERE submitted = 1
                      AND original_run_id IS NOT NULL
                      AND created BETWEEN %(start_date)s AND %(end_date)s
                    UNION
                    SELECT id FROM dbo.claims_deleted
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND created BETWEEN %(start_date)s AND %(end_date)s
                ),
                prior_draft AS (
                    SELECT id FROM Claims
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND created BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                    UNION
                    SELECT id FROM Claims
                    WHERE submitted = 1
                      AND original_run_id IS NOT NULL
                      AND created BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                    UNION
                    SELECT id FROM dbo.claims_deleted
                    WHERE submitted = 0
                      AND original_run_id IS NULL
                      AND created BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                )
                SELECT 'Selected Period' AS Period, COUNT(*) AS DraftsCreated
                FROM selected_draft
                UNION ALL
                SELECT 'Prior Period' AS Period, COUNT(*) AS DraftsCreated
                FROM prior_draft
                """,
                "layout": {"x": 0, "y": 14, "w": 6, "h": 4},
                "config": {"xAxisKey": "Period", "yAxisKeys": ["DraftsCreated"], "colors": ["#14b8a6"]},
            },
            {
                "id": "claims-submitted-period-comparison",
                "title": "Drafts Submitted During This Period",
                "type": "table",
                "sql_query": """
                WITH selected_submitted AS (
                    SELECT id FROM Claims
                    WHERE submitted = 1
                      AND original_run_id IS NULL
                      AND date_of_submitted BETWEEN %(start_date)s AND %(end_date)s
                ),
                prior_submitted AS (
                    SELECT id FROM Claims
                    WHERE submitted = 1
                      AND original_run_id IS NULL
                      AND date_of_submitted BETWEEN %(prior_start_date)s AND %(prior_end_date)s
                )
                SELECT 'Selected Period' AS Period, COUNT(*) AS DraftsSubmitted
                FROM selected_submitted
                UNION ALL
                SELECT 'Prior Period' AS Period, COUNT(*) AS DraftsSubmitted
                FROM prior_submitted
                """,
                "layout": {"x": 6, "y": 14, "w": 6, "h": 4},
                "config": {"xAxisKey": "Period", "yAxisKeys": ["DraftsSubmitted"], "colors": ["#22c55e"]},
            },

            # ── Row 6: monthly trend ──────────────────────────────────
            {
                "id": "claims-monthly-trend",
                "title": "Monthly Claims Trend (YTD)",
                "type": "line",
                "sql_query": f"""
                SELECT
                    FORMAT(c.created, 'MMM') AS Month,
                    COUNT(DISTINCT c.id) AS Claims
                FROM Claims c
                WHERE c.submitted = 1
                  AND c.archived = 0
                  AND c.created >= %(ytd_start)s
                  AND c.created <= %(end_date)s
                GROUP BY FORMAT(c.created, 'MMM'), MONTH(c.created)
                ORDER BY MONTH(c.created)
                """,
                "layout": {"x": 0, "y": 18, "w": 12, "h": 4},
                "config": {"xAxisKey": "Month", "yAxisKeys": ["Claims"], "colors": ["#6366f1"]},
            },

            # NOTE: The "Top Fire Departments by Drafts" grid has been moved
            # to the standalone AI Adoption dashboard (see /api/ai-adoption/departments).
        ],
    }


async def _seed_default_dashboards() -> None:
    """Upserts the system-managed claims dashboard on every startup.

    The dashboard definition lives in code (``_build_default_claims_dashboard``)
    so that query fixes and widget changes take effect immediately without
    requiring a manual MongoDB edit.  User-created dashboards (those without
    ``created_by: "system"``) are never touched.
    """
    if os.getenv("TESTING") == "true":
        return

    dashboards = db_manager.db["dashboards"]
    now = datetime.now(UTC)
    payload = _build_default_claims_dashboard()

    # Match the current name OR any legacy names so that a rename in code
    # migrates the existing system dashboard in place instead of leaving an
    # orphaned copy behind.
    legacy_names = {"Claims Calendar-Year Overview"}
    existing_system = await dashboards.find(
        {"created_by": "system", "name": {"$in": [payload["name"], *legacy_names]}}
    ).sort("created_at", 1).to_list(length=100)
    existing = existing_system[0] if existing_system else None
    if existing:
        # Preserve original creation timestamp, update everything else
        payload["updated_at"] = now
        await dashboards.update_one(
            {"_id": existing["_id"]},
            {"$set": payload},
        )
        duplicate_ids = [dashboard["_id"] for dashboard in existing_system[1:]]
        if duplicate_ids:
            await dashboards.delete_many({"_id": {"$in": duplicate_ids}})
            logger.info("Removed %d duplicate system dashboards", len(duplicate_ids))
        logger.info("Updated system dashboard: %s", payload["name"])
    else:
        payload["created_by"] = "system"
        payload["created_at"] = now
        payload["updated_at"] = now
        await dashboards.insert_one(payload)
        logger.info("Seeded default claims dashboard: %s", payload["name"])

# --- DASHBOARD METADATA ENDPOINTS ---

@app.get(
    "/api/dashboards", 
    response_model=List[DashboardResponse],
    dependencies=[Depends(get_current_user)]
)
async def list_dashboards(db = Depends(get_db)):
    """Retrieves system dashboards once, followed by user-saved dashboards."""
    cursor = db["dashboards"].find().sort("created_at", -1)
    dashboards = await cursor.to_list(length=100)
    seen_system_names = set()
    system_dashboards = []
    saved_dashboards = []
    for dashboard in dashboards:
        if dashboard.get("created_by") == "system":
            name = dashboard.get("name")
            if name in seen_system_names:
                continue
            seen_system_names.add(name)
            system_dashboards.append(dashboard)
        else:
            saved_dashboards.append(dashboard)
    return [
        serialize_mongo_doc(dash)
        for dash in [*system_dashboards, *saved_dashboards]
    ]

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
    doc["created_at"] = datetime.now(UTC)
    doc["updated_at"] = datetime.now(UTC)
    
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
        
    existing = await db["dashboards"].find_one({"_id": ObjectId(dashboard_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    if existing.get("created_by") == "system":
        raise HTTPException(status_code=403, detail="System dashboards cannot be changed.")

    doc = dashboard.model_dump()
    doc["updated_at"] = datetime.now(UTC)
    
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
        
    existing = await db["dashboards"].find_one({"_id": ObjectId(dashboard_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
    if existing.get("created_by") == "system":
        raise HTTPException(status_code=403, detail="System dashboards cannot be deleted.")

    result = await db["dashboards"].delete_one({"_id": ObjectId(dashboard_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Dashboard not found.")
        
    return {"success": True}

# --- AZURE SQL EXECUTION ENDPOINTS ---

@app.post(
    "/api/query/sql",
    dependencies=[Depends(get_current_user)]
)
def run_query(request: SQLQueryRequest):
    """Runs a read-only parameterized query against the target database.

    Declared as a sync ``def`` so FastAPI dispatches it to the thread-pool,
    allowing multiple widget queries to execute concurrently instead of
    blocking the async event loop one-by-one.
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
def get_schema():
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
def get_filters():
    """Loads active filter options from database tables."""
    try:
        return target_db.get_filter_dropdown_options()
    except Exception as e:
        logger.error(f"Filter options query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get(
    "/api/server-date",
    dependencies=[Depends(get_current_user)]
)
def get_server_date():
    """Returns the database server's current date via GETDATE().

    The frontend uses this to display accurate date ranges aligned with the
    same clock the backend uses for query parameters.
    """
    try:
        server_date = target_db.get_server_date()
        return {"date": server_date.isoformat()}
    except Exception as e:
        logger.error(f"Failed to fetch server date: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.post(
    "/api/query/drilldown",
    dependencies=[Depends(get_current_user)]
)
def run_drilldown(request: DrillDownRequest):
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
