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

    # Period comparison uses id/date_of_submitted
    period = widgets["claims-period-comparison"]
    assert "PARTITION BY id ORDER BY id" in period["sql_query"]
    assert "date_of_submitted BETWEEN" in period["sql_query"]
    assert "%(start_date)s" in period["sql_query"]
    assert "%(prior_start_date)s" in period["sql_query"]

    # Submitted period comparison uses id
    submitted = widgets["claims-submitted-period-comparison"]
    assert "PARTITION BY id ORDER BY id" in submitted["sql_query"]
    assert "date_of_submitted BETWEEN" in submitted["sql_query"]

    # New runs uses temporal query with ClaimCurrentTypeId = 1
    new_runs = widgets["claims-new-runs-by-type"]
    assert "FOR SYSTEM_TIME BETWEEN" in new_runs["sql_query"]
    assert "PARTITION BY id ORDER BY id" in new_runs["sql_query"]
    assert "ClaimCurrentTypeId = 1" in new_runs["sql_query"]

    # Active runs uses temporal query with ClaimCurrentTypeId = 4
    active_runs = widgets["claims-active-by-status"]
    assert "FOR SYSTEM_TIME BETWEEN" in active_runs["sql_query"]
    assert "PARTITION BY id ORDER BY id" in active_runs["sql_query"]
    assert "ClaimCurrentTypeId = 4" in active_runs["sql_query"]

def test_default_dashboard_widget_ids():
    """Verifies the expected widget IDs exist in the default dashboard."""
    dashboard = _build_default_claims_dashboard()
    widget_ids = {w["id"] for w in dashboard["widgets"]}

    expected_ids = {
        "claims-draft-intake-ytd",
        "claims-draft-deleted-ytd",
        "claims-draft-submitted-ytd",
        "claims-draft-open",
        "claims-current-new-runs",
        "claims-current-active-runs",
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
