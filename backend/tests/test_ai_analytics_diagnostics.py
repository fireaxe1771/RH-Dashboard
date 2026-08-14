"""Tests for AI Analytics diagnostics service and routes."""

import pytest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer valid-mock-token"}


# ---------------------------------------------------------------------------
# Diagnostics service tests
# ---------------------------------------------------------------------------

class TestDiagnosticsService:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_diagnostics_summary_empty(self, mock_logs, mock_canc, mock_mongo, mock_cohort):
        from ai_analytics.diagnostics_service import get_diagnostics_summary
        from ai_analytics.models import AiAnalyticsFilters

        mock_cohort.return_value = []
        filters = AiAnalyticsFilters()
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(get_diagnostics_summary(None, filters))
        assert result.ai_runs == 0
        assert result.completed == 0
        assert result.errors == 0
        assert result.avg_duration is None

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_diagnostics_summary_with_data(self, mock_logs, mock_canc, mock_mongo, mock_cohort):
        from ai_analytics.diagnostics_service import get_diagnostics_summary
        from ai_analytics.models import AiAnalyticsFilters

        mock_cohort.return_value = [
            {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-15T10:00:00"},
            {"claim_id": 200, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-16T10:00:00"},
        ]
        mock_mongo.return_value = {
            100: {"claim_processing_status": "COMPLETED", "agent_exec_status": "success",
                  "confidence_level": 90, "line_items_save_to_rh_status": True,
                  "retry_count": 0, "processing_time_seconds": 15.5},
            200: {"claim_processing_status": "COMPLETED", "agent_exec_status": "success",
                  "confidence_level": 30, "line_items_save_to_rh_status": False,
                  "retry_count": 2, "processing_time_seconds": 30.0},
        }
        mock_canc.return_value = {}
        mock_logs.return_value = {
            100: [{"log_text": "Invoice to Insurance - Released", "user_id": 7486, "user_type_id": 2}],
            200: [{"log_text": "Invoice to Insurance - Cancelled", "user_id": 7486, "user_type_id": 2}],
        }

        filters = AiAnalyticsFilters()
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(get_diagnostics_summary(None, filters))
        assert result.ai_runs == 2
        assert result.completed == 2
        assert result.retries == 1  # claim 200 has retry_count=2
        assert result.low_confidence == 1  # claim 200 has confidence=30
        assert result.writeback_failures == 1  # claim 200 has writeback=False
        assert result.avg_duration == 22.75  # (15.5 + 30.0) / 2

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_sql_detail_failure_marks_data_incomplete(
        self, mock_logs, mock_canc, mock_mongo, mock_cohort,
    ):
        from ai_analytics.diagnostics_service import get_diagnostics_summary
        from ai_analytics.models import AiAnalyticsFilters
        import asyncio

        mock_cohort.return_value = [{"claim_id": 100, "AI_inv_process_status": 4}]
        mock_mongo.return_value = {}
        mock_canc.side_effect = RuntimeError("cancellation query failed")
        mock_logs.return_value = {}

        result = asyncio.get_event_loop().run_until_complete(
            get_diagnostics_summary(None, AiAnalyticsFilters())
        )
        assert result.data_complete is False
        assert result.source_status["recoveryhub_sql"] == "partial"


# ---------------------------------------------------------------------------
# Diagnostics route tests
# ---------------------------------------------------------------------------

class TestDiagnosticsRoutes:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_diagnostics_summary_route(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = []
        response = test_client.get("/api/ai-analytics/diagnostics/summary", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["ai_runs"] == 0
        assert data["completed"] == 0

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_diagnostics_confidence_route(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = [
            {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-15T10:00:00"},
        ]
        mock_mongo.return_value = {
            100: {"claim_processing_status": "COMPLETED", "confidence_level": 85,
                  "line_items_save_to_rh_status": True},
        }
        mock_canc.return_value = {}
        mock_logs.return_value = {
            100: [{"log_text": "Invoice to Insurance - Released", "user_id": 7486, "user_type_id": 2}],
        }

        response = test_client.get("/api/ai-analytics/diagnostics/confidence", headers=AUTH)
        assert response.status_code == 200
        buckets = response.json()
        assert len(buckets) == 1
        assert buckets[0]["bucket"] == "80-89"
        assert buckets[0]["count"] == 1
        assert buckets[0]["released"] == 1

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_diagnostics_retries_route(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = []
        response = test_client.get("/api/ai-analytics/diagnostics/retries", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["total_records"] == 0
        assert data["records_with_retries"] == 0


# ---------------------------------------------------------------------------
# Phase 10 projection service path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_stats_uses_projection_when_flag_enabled(monkeypatch):
    """The service-level flag branch must call projection aggregation, not raw Mongo."""
    from ai_analytics.diagnostics_service import get_agent_stats
    from ai_analytics.models import AiAnalyticsFilters
    from config import settings

    monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)
    projection_results = [{
        "agent": "agent-a",
        "status": "completed",
        "processing_stage": "stage-1",
        "request_type": "incident_analysis",
        "count": 3,
    }]

    with patch(
        "ai_analytics.diagnostics_service.projection_repo.aggregate_agent_stats_from_projections",
        new_callable=AsyncMock,
        return_value=projection_results,
    ) as aggregate, patch(
        "ai_analytics.diagnostics_service.mongo_repo.AGENT_CONVERSATIONS_COLLECTION",
        "should-not-be-used",
    ):
        stats = await get_agent_stats(object(), AiAnalyticsFilters())

    aggregate.assert_awaited_once()
    assert stats[0].agent == "agent-a"
    assert stats[0].count == 3


@pytest.mark.asyncio
async def test_agent_stats_projection_failure_returns_empty(monkeypatch):
    """Projection aggregation errors are contained at the diagnostics boundary."""
    from ai_analytics.diagnostics_service import get_agent_stats
    from ai_analytics.models import AiAnalyticsFilters
    from config import settings

    monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)
    with patch(
        "ai_analytics.diagnostics_service.projection_repo.aggregate_agent_stats_from_projections",
        new_callable=AsyncMock,
        side_effect=RuntimeError("projection unavailable"),
    ):
        assert await get_agent_stats(object(), AiAnalyticsFilters()) == []

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_diagnostics_writeback_route(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = []
        response = test_client.get("/api/ai-analytics/diagnostics/writeback", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["total_records"] == 0
        assert data["failure_count"] == 0

    def test_diagnostics_agents_route(self, test_client):
        """Agent stats route — uses mock mongo from conftest."""
        response = test_client.get("/api/ai-analytics/diagnostics/agents", headers=AUTH)
        assert response.status_code == 200
        # With empty mock mongo, should return empty list
        data = response.json()
        assert isinstance(data, list)
