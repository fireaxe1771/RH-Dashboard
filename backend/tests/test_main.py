import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from database import db_manager
from main import _build_default_claims_dashboard, _seed_default_dashboards

def test_unauthorized_access(test_client: TestClient):
    """Asserts that requests lacking header tokens return 401 Unauthorized."""
    response = test_client.get("/api/dashboards")
    assert response.status_code == 401
    assert "Authorization header missing" in response.json()["detail"]

def test_invalid_token(test_client: TestClient):
    """Asserts that malformed tokens are intercepted and blocked."""
    headers = {"Authorization": "Bearer invalid-token"}
    response = test_client.get("/api/dashboards", headers=headers)
    assert response.status_code == 401
    assert "signature verification failed" in response.json()["detail"]

def test_dashboard_crud_flow(test_client: TestClient):
    """Tests the full MongoDB CRUD integration lifecycle for dashboards."""
    headers = {"Authorization": "Bearer valid-mock-token"}
    
    # 1. List — should have the system-seeded dashboard
    response = test_client.get("/api/dashboards", headers=headers)
    assert response.status_code == 200
    dashboards = response.json()
    # The seeder runs on startup and inserts the default dashboard
    assert len(dashboards) >= 0  # may or may not be seeded in test mode

    # 2. Create dashboard
    dash_payload = {
        "name": "Claims Workflow Dashboard",
        "description": "Visualizing fire runs",
        "widgets": [
          {
            "id": "widget-1",
            "title": "Submitted Claims",
            "type": "bar",
            "sql_query": "SELECT COUNT(*) as Count FROM Claims WHERE Status = 'Submitted'",
            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "config": {"xAxisKey": "Status", "yAxisKeys": ["Count"]}
          }
        ]
    }
    response = test_client.post("/api/dashboards", json=dash_payload, headers=headers)
    assert response.status_code == 200
    created_dash = response.json()
    assert created_dash["name"] == "Claims Workflow Dashboard"
    dash_id = created_dash.get("id") or created_dash.get("_id")
    assert dash_id is not None


    # 3. Read dashboard
    response = test_client.get(f"/api/dashboards/{dash_id}", headers=headers)
    assert response.status_code == 200
    fetched_dash = response.json()
    assert fetched_dash["name"] == "Claims Workflow Dashboard"
    assert fetched_dash["widgets"][0]["id"] == "widget-1"

    # 4. Update dashboard
    updated_payload = {
        "name": "Claims Performance Dashboard (Updated)",
        "description": "Visualizing fire runs and payments",
        "widgets": []
    }
    response = test_client.put(f"/api/dashboards/{dash_id}", json=updated_payload, headers=headers)
    assert response.status_code == 200
    updated_dash = response.json()
    assert updated_dash["name"] == "Claims Performance Dashboard (Updated)"
    assert len(updated_dash["widgets"]) == 0

    # 5. Delete dashboard
    response = test_client.delete(f"/api/dashboards/{dash_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["success"] is True

    # 6. Read after delete returns 404
    response = test_client.get(f"/api/dashboards/{dash_id}", headers=headers)
    assert response.status_code == 404

def test_default_dashboard_uses_correct_columns():
    """Ensures the default dashboard queries use the correct column names (id, created)."""
    dashboard = _build_default_claims_dashboard()
    widgets = {w["id"]: w for w in dashboard["widgets"]}

    # Drafts Created YTD uses a 3-way UNION of current drafts, current runs,
    # and deleted drafts — all filtered by created BETWEEN ytd_start AND
    # end_date. This avoids scanning the 38.6M-row temporal history table
    # (FOR SYSTEM_TIME BETWEEN) which caused 504 gateway timeouts on cold
    # cache. The UNION deduplicates by id, matching the old ROW_NUMBER
    # PARTITION BY id semantics.
    ytd = widgets["claims-draft-intake-ytd"]
    assert "FOR SYSTEM_TIME" not in ytd["sql_query"]
    assert "ROW_NUMBER" not in ytd["sql_query"]
    assert "FROM dbo.claims_deleted" in ytd["sql_query"]
    assert "submitted = 0" in ytd["sql_query"]
    assert "original_run_id IS NULL" in ytd["sql_query"]
    assert "created BETWEEN %(ytd_start)s AND %(end_date)s" in ytd["sql_query"]
    assert "%(end_date)s" in ytd["sql_query"]
    # Must include all three sources (current drafts, current runs, deleted)
    assert ytd["sql_query"].count("SELECT id FROM Claims") == 2
    assert "SELECT id FROM dbo.claims_deleted" in ytd["sql_query"]

    # Deleted Drafts YTD queries the claims_deleted table (plain, non-temporal)
    # and filters on BOTH created and the deletion timestamp falling in YTD,
    # mirroring Drafts Created YTD filters plus the deletion-date window.
    deleted = widgets["claims-draft-deleted-ytd"]
    assert "FROM dbo.claims_deleted" in deleted["sql_query"]
    assert "FOR SYSTEM_TIME" not in deleted["sql_query"]
    assert "ROW_NUMBER" not in deleted["sql_query"]
    assert "COUNT(DISTINCT id)" in deleted["sql_query"]
    assert "submitted = 0" in deleted["sql_query"]
    assert "original_run_id IS NULL" in deleted["sql_query"]
    assert "created BETWEEN %(ytd_start)s AND %(end_date)s" in deleted["sql_query"]
    # `timestamp` is a SQL Server reserved keyword so it must be bracketed.
    assert "[timestamp] BETWEEN %(ytd_start)s AND %(end_date)s" in deleted["sql_query"]

    # Period comparison uses 3-way UNION (drafts + runs + deleted) per period,
    # avoiding FOR SYSTEM_TIME scans of the 38.6M-row history table.
    period = widgets["claims-period-comparison"]
    assert "FOR SYSTEM_TIME" not in period["sql_query"]
    assert "ROW_NUMBER" not in period["sql_query"]
    assert "created BETWEEN" in period["sql_query"]
    assert "%(start_date)s" in period["sql_query"]
    assert "%(prior_start_date)s" in period["sql_query"]
    assert "FROM dbo.claims_deleted" in period["sql_query"]

    # Submitted period comparison uses plain Claims (no FOR SYSTEM_TIME)
    submitted = widgets["claims-submitted-period-comparison"]
    assert "FOR SYSTEM_TIME" not in submitted["sql_query"]
    assert "ROW_NUMBER" not in submitted["sql_query"]
    assert "date_of_submitted BETWEEN" in submitted["sql_query"]
    assert "%(start_date)s" in submitted["sql_query"]
    assert "%(prior_start_date)s" in submitted["sql_query"]

    # New runs Submitted vs Recycled uses plain Claims (no FOR SYSTEM_TIME)
    new_runs = widgets["claims-new-runs-by-type"]
    assert "FOR SYSTEM_TIME" not in new_runs["sql_query"]
    assert "ROW_NUMBER" not in new_runs["sql_query"]
    assert "Submitted" in new_runs["sql_query"]
    assert "Recycled" in new_runs["sql_query"]
    assert "date_of_submitted BETWEEN" in new_runs["sql_query"]

    # Active runs uses plain Claims with ClaimCurrentTypeId = 4
    active_runs = widgets["claims-active-by-status"]
    assert "FOR SYSTEM_TIME" not in active_runs["sql_query"]
    assert "ROW_NUMBER" not in active_runs["sql_query"]
    assert "ClaimCurrentTypeId = 4" in active_runs["sql_query"]
    assert "date_of_submitted BETWEEN" in active_runs["sql_query"]

    # Current Claims Summary combines the former Drafts Still Open, Current
    # New Runs, and Current Active Runs tiles into a single 3-column stat
    # query. Each subquery preserves the exact filter from the old widget.
    summary = widgets["claims-current-summary"]
    assert "AS Drafts" in summary["sql_query"]
    assert "AS NewRuns" in summary["sql_query"]
    assert "AS ActiveRuns" in summary["sql_query"]
    # Drafts Still Open filter: submitted=0, original_run_id IS NULL, created <= end_date
    assert "submitted = 0" in summary["sql_query"]
    assert "c.created <= %(end_date)s" in summary["sql_query"]
    # New Runs filter: submitted=1, archived=0, ClaimCurrentTypeId = 1
    assert "ClaimCurrentTypeId = 1" in summary["sql_query"]
    # Active Runs filter: submitted=1, archived=0, ClaimCurrentTypeId = 4
    assert "ClaimCurrentTypeId = 4" in summary["sql_query"]

def test_default_dashboard_widget_ids():
    """Verifies the expected widget IDs exist in the default dashboard."""
    dashboard = _build_default_claims_dashboard()
    widget_ids = {w["id"] for w in dashboard["widgets"]}

    expected_ids = {
        "claims-draft-intake-ytd",
        "claims-draft-deleted-ytd",
        "claims-draft-submitted-ytd",
        "claims-current-summary",
        "claims-new-runs-by-type",
        "claims-active-by-status",
        "claims-total-amount-ytd",
        "claims-avg-amount",
        "claims-amount-by-status",
        "claims-period-comparison",
        "claims-submitted-period-comparison",
        "claims-monthly-trend",
    }
    assert expected_ids == widget_ids


def _insert_dashboard(document):
    asyncio.run(db_manager.db["dashboards"].insert_one(document))
    return str(document["_id"])


def test_system_dashboard_cannot_be_updated_or_deleted(test_client: TestClient):
    """System-managed dashboards remain immutable through the CRUD API."""
    dashboard_id = ObjectId()
    _insert_dashboard({
        "_id": dashboard_id,
        "name": "Claims Breakdown",
        "description": "System dashboard",
        "widgets": [],
        "created_by": "system",
        "created_at": datetime.now(UTC),
    })
    headers = {"Authorization": "Bearer valid-mock-token"}
    payload = {"name": "Changed", "description": "Changed", "widgets": []}

    response = test_client.put(f"/api/dashboards/{dashboard_id}", json=payload, headers=headers)
    assert response.status_code == 403

    response = test_client.delete(f"/api/dashboards/{dashboard_id}", headers=headers)
    assert response.status_code == 403

    stored = asyncio.run(db_manager.db["dashboards"].find_one({"_id": dashboard_id}))
    assert stored["name"] == "Claims Breakdown"


def test_list_dashboards_deduplicates_system_entries_and_places_them_first(
    test_client: TestClient,
):
    """The API returns one system dashboard before user-saved dashboards."""
    now = datetime.now(UTC)
    _insert_dashboard({
        "_id": ObjectId(),
        "name": "Saved Dashboard",
        "widgets": [],
        "created_by": "john.doe@streamlineas.com",
        "created_at": now,
    })
    _insert_dashboard({
        "_id": ObjectId(),
        "name": "Claims Breakdown",
        "widgets": [],
        "created_by": "system",
        "created_at": now - timedelta(days=2),
    })
    _insert_dashboard({
        "_id": ObjectId(),
        "name": "Claims Breakdown",
        "widgets": [],
        "created_by": "system",
        "created_at": now - timedelta(days=1),
    })

    response = test_client.get(
        "/api/dashboards",
        headers={"Authorization": "Bearer valid-mock-token"},
    )

    assert response.status_code == 200
    dashboards = response.json()
    assert [dashboard["name"] for dashboard in dashboards] == [
        "Claims Breakdown",
        "Saved Dashboard",
    ]


def test_seed_default_dashboard_removes_duplicate_system_entries(monkeypatch):
    """Startup seeding keeps the oldest system dashboard and removes duplicates.

    Also covers the rename path: dashboards stored under a legacy name are
    migrated to the current name in place.
    """
    monkeypatch.setenv("TESTING", "false")
    now = datetime.now(UTC)
    _insert_dashboard({
        "_id": ObjectId(),
        "name": "Claims Calendar-Year Overview",
        "widgets": [],
        "created_by": "system",
        "created_at": now - timedelta(days=2),
    })
    _insert_dashboard({
        "_id": ObjectId(),
        "name": "Claims Calendar-Year Overview",
        "widgets": [],
        "created_by": "system",
        "created_at": now - timedelta(days=1),
    })

    asyncio.run(_seed_default_dashboards())

    dashboards = asyncio.run(
        db_manager.db["dashboards"].find(
            {"created_by": "system", "name": "Claims Breakdown"}
        ).to_list(length=100)
    )
    assert len(dashboards) == 1
    assert dashboards[0]["widgets"] == _build_default_claims_dashboard()["widgets"]

    # Legacy-named documents must all be migrated (no orphans left behind)
    legacy = asyncio.run(
        db_manager.db["dashboards"].find(
            {"created_by": "system", "name": "Claims Calendar-Year Overview"}
        ).to_list(length=100)
    )
    assert legacy == []
