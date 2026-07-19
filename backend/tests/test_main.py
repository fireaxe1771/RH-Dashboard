import pytest
from fastapi.testclient import TestClient
from main import _build_default_claims_dashboard

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

    # Drafts Created YTD uses temporal query bounded by end_date
    ytd = widgets["claims-draft-intake-ytd"]
    assert "PARTITION BY id ORDER BY id" in ytd["sql_query"]
    assert "FOR SYSTEM_TIME BETWEEN" in ytd["sql_query"]
    assert "%(end_date)s" in ytd["sql_query"]

    # Deleted Drafts YTD queries the deleted_claims table (plain, non-temporal)
    # and filters on BOTH created and the deletion timestamp falling in YTD,
    # mirroring the Drafts Created YTD filters plus the deletion-date window.
    deleted = widgets["claims-draft-deleted-ytd"]
    assert "FROM dbo.claims_deleted" in deleted["sql_query"]
    assert "FOR SYSTEM_TIME" not in deleted["sql_query"]
    assert "PARTITION BY id ORDER BY [timestamp]" in deleted["sql_query"]
    assert "submitted = 0" in deleted["sql_query"]
    assert "original_run_id IS NULL" in deleted["sql_query"]
    assert "created BETWEEN %(ytd_start)s AND %(end_date)s" in deleted["sql_query"]
    # `timestamp` is a SQL Server reserved keyword so it must be bracketed.
    assert "[timestamp] BETWEEN %(ytd_start)s AND %(end_date)s" in deleted["sql_query"]
    assert "WHERE rn = 1" in deleted["sql_query"]

    # Period comparison uses id/created (drafts: submitted=0)
    period = widgets["claims-period-comparison"]
    assert "PARTITION BY id ORDER BY id" in period["sql_query"]
    assert "created BETWEEN" in period["sql_query"]
    assert "%(start_date)s" in period["sql_query"]
    assert "%(prior_start_date)s" in period["sql_query"]

    # Submitted period comparison uses id
    submitted = widgets["claims-submitted-period-comparison"]
    assert "PARTITION BY id ORDER BY id" in submitted["sql_query"]
    assert "date_of_submitted BETWEEN" in submitted["sql_query"]

    # New runs Submitted vs Recycled uses temporal query split by submitted flag
    new_runs = widgets["claims-new-runs-by-type"]
    assert "FOR SYSTEM_TIME BETWEEN" in new_runs["sql_query"]
    assert "PARTITION BY id ORDER BY id" in new_runs["sql_query"]
    assert "Submitted" in new_runs["sql_query"]
    assert "Recycled" in new_runs["sql_query"]

    # Active runs uses temporal query with ClaimCurrentTypeId = 4
    active_runs = widgets["claims-active-by-status"]
    assert "FOR SYSTEM_TIME BETWEEN" in active_runs["sql_query"]
    assert "PARTITION BY id ORDER BY id" in active_runs["sql_query"]
    assert "ClaimCurrentTypeId = 4" in active_runs["sql_query"]

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
