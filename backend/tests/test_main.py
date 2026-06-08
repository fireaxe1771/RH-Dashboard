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
    
    # 1. List initially empty
    response = test_client.get("/api/dashboards", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 0

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
    assert created_dash["id"] is not None
    dash_id = created_dash["id"]

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

def test_default_status_widget_uses_ytd_distinct_runs():
    """Ensures the default status widget stays aligned to the YTD submitted-run metric."""
    dashboard = _build_default_claims_dashboard()
    status_widget = next(widget for widget in dashboard["widgets"] if widget["id"] == "claims-new-runs-by-status")

    assert "COUNT(DISTINCT c.original_run_id)" in status_widget["sql_query"]
    assert "c.DateCreated >=" in status_widget["sql_query"]
    assert "StatusLabel" in status_widget["sql_query"]
    assert status_widget["config"]["xAxisKey"] == "StatusLabel"

def test_default_draft_summary_widgets_are_aligned():
    """Ensures the default draft summary cards follow the intended current-vs-YTD split."""
    dashboard = _build_default_claims_dashboard()
    total_widget = next(widget for widget in dashboard["widgets"] if widget["id"] == "claims-total-drafts")
    created_widget = next(widget for widget in dashboard["widgets"] if widget["id"] == "claims-draft-intake-ytd")
    submitted_widget = next(widget for widget in dashboard["widgets"] if widget["id"] == "claims-draft-submitted-ytd")
    remaining_widget = next(widget for widget in dashboard["widgets"] if widget["id"] == "claims-draft-open-ytd")

    assert "COUNT(DISTINCT c.ClaimID)" in total_widget["sql_query"]
    assert "c.submitted = 0" in total_widget["sql_query"]
    assert "c.original_run_id IS NULL" in total_widget["sql_query"]
    assert "c.archived = 0" in total_widget["sql_query"]
    assert "FOR SYSTEM_TIME ALL" in created_widget["sql_query"]
    assert "ROW_NUMBER() OVER (PARTITION BY c.ClaimID" in created_widget["sql_query"]
    assert "FOR SYSTEM_TIME ALL" in submitted_widget["sql_query"]
    assert "ROW_NUMBER() OVER (PARTITION BY c.original_run_id" in submitted_widget["sql_query"]
    assert "c.submitted = 0" in remaining_widget["sql_query"]
    assert "c.archived = 0" in remaining_widget["sql_query"]
    assert "c.DateCreated >= " in remaining_widget["sql_query"]
    assert total_widget["layout"]["w"] == 3
    assert created_widget["layout"]["w"] == 3
    assert submitted_widget["layout"]["w"] == 3
    assert remaining_widget["layout"]["w"] == 3
