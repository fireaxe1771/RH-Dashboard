"""Integration tests for AI Analytics routes.

Uses the test_client fixture and mocks the SQL + Mongo data layers.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer valid-mock-token"}


# ---------------------------------------------------------------------------
# Summary endpoint
# ---------------------------------------------------------------------------

class TestOutcomesSummaryRoute:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_summary_empty_cohort(
        self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client,
    ):
        mock_cohort.return_value = []
        response = test_client.get("/api/ai-analytics/outcomes/summary", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["total_ai_invoices"] == 0
        assert data["released"] == 0
        assert data["cancelled_rejected"] == 0
        assert data["business_release_rate"] == 0.0

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_summary_with_data(
        self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client,
    ):
        # 3 claims: 1 released, 1 cancelled, 1 pending
        mock_cohort.return_value = [
            {
                "claim_id": 100, "AI_inv_process_status": 4,
                "dept_id": 1, "department_name": "FD1",
                "department_state": "TX", "run_number": "R1",
                "invoice_number": "INV1", "amount_invoiced": 500.0,
                "claim_created_at": "2026-01-01",
                "ai_business_updated_at": "2026-01-15T10:00:00",
            },
            {
                "claim_id": 200, "AI_inv_process_status": 4,
                "dept_id": 1, "department_name": "FD1",
                "department_state": "TX", "run_number": "R2",
                "invoice_number": "INV2", "amount_invoiced": 300.0,
                "claim_created_at": "2026-01-02",
                "ai_business_updated_at": "2026-01-16T10:00:00",
            },
            {
                "claim_id": 300, "AI_inv_process_status": 2,
                "dept_id": 1, "department_name": "FD1",
                "department_state": "TX", "run_number": "R3",
                "invoice_number": None, "amount_invoiced": 0.0,
                "claim_created_at": "2026-01-03",
                "ai_business_updated_at": "2026-01-17T10:00:00",
            },
        ]
        mock_mongo.return_value = {
            100: {"claim_processing_status": "COMPLETED", "agent_exec_status": "success",
                  "confidence_level": 90, "line_items_save_to_rh_status": True,
                  "billing_category": "Motor Vehicle Accident", "retry_count": 0},
            200: {"claim_processing_status": "COMPLETED", "agent_exec_status": "success",
                  "confidence_level": 50, "line_items_save_to_rh_status": True,
                  "billing_category": "Motor Vehicle Accident", "retry_count": 0},
            300: {"claim_processing_status": "INITIATED", "agent_exec_status": "in_progress",
                  "confidence_level": None, "line_items_save_to_rh_status": False,
                  "billing_category": None, "retry_count": 0},
        }
        mock_canc.return_value = {
            200: {"reason_id": 7, "raw_reason": "Miscalculated Nested Line Items",
                  "reason_descr": "Wrong qty"},
        }
        mock_logs.return_value = {
            100: [{"log_text": "Invoice to Insurance - Released", "user_id": 7486, "user_type_id": 2}],
            200: [{"log_text": "Invoice to Insurance - Cancelled", "user_id": 7486, "user_type_id": 2}],
            300: [{"log_text": "Line Item Created", "user_id": 10499, "user_type_id": 1}],
        }

        response = test_client.get("/api/ai-analytics/outcomes/summary", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["total_ai_invoices"] == 3
        assert data["released"] == 1
        assert data["cancelled_rejected"] == 1
        assert data["pending"] == 1
        assert data["terminal_count"] == 2
        assert data["business_release_rate"] == 50.0
        assert data["rejection_rate"] == 50.0
        assert data["ai_completed"] == 2
        assert data["writeback_success"] == 2


# ---------------------------------------------------------------------------
# Funnel endpoint
# ---------------------------------------------------------------------------

class TestOutcomesFunnelRoute:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_funnel_empty(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = []
        response = test_client.get("/api/ai-analytics/outcomes/funnel", headers=AUTH)
        assert response.status_code == 200
        stages = response.json()
        assert len(stages) == 7
        assert all(s["count"] == 0 for s in stages)

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_funnel_with_data(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = [
            {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-15T10:00:00"},
        ]
        mock_mongo.return_value = {
            100: {"claim_processing_status": "COMPLETED", "agent_exec_status": "success",
                  "confidence_level": 90, "line_items_save_to_rh_status": True,
                  "billing_category": "Motor Vehicle Accident", "retry_count": 0},
        }
        mock_canc.return_value = {}
        mock_logs.return_value = {
            100: [{"log_text": "Invoice to Insurance - Released", "user_id": 7486, "user_type_id": 2}],
        }

        response = test_client.get("/api/ai-analytics/outcomes/funnel", headers=AUTH)
        assert response.status_code == 200
        stages = response.json()
        assert stages[0]["count"] == 1  # Entered RH AI workflow
        assert stages[1]["count"] == 1  # Mongo AI record found
        assert stages[2]["count"] == 1  # Billability determined
        assert stages[3]["count"] == 1  # AI processing completed
        assert stages[4]["count"] == 1  # Line items saved to RH
        assert stages[5]["count"] == 1  # Business released
        assert stages[6]["count"] == 0  # Business cancelled/rejected


# ---------------------------------------------------------------------------
# Rejection reasons endpoint
# ---------------------------------------------------------------------------

class TestRejectionReasonsRoute:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_rejection_reasons(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = [
            {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-15T10:00:00"},
            {"claim_id": 200, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-16T10:00:00"},
        ]
        mock_mongo.return_value = {}
        mock_canc.return_value = {
            100: {"reason_id": 7, "raw_reason": "Miscalculated Nested Line Items",
                  "reason_descr": "Wrong qty"},
            200: {"reason_id": 1, "raw_reason": "Incorrect line item description",
                  "reason_descr": "Typo"},
        }
        mock_logs.return_value = {
            100: [{"log_text": "Invoice to Insurance - Cancelled", "user_id": 7486, "user_type_id": 2}],
            200: [{"log_text": "Invoice to Insurance - Cancelled", "user_id": 7486, "user_type_id": 2}],
        }

        response = test_client.get("/api/ai-analytics/outcomes/rejection-reasons", headers=AUTH)
        assert response.status_code == 200
        stats = response.json()
        assert len(stats) == 2
        # Sorted by count descending — both have count 1, so order may vary
        categories = {s["normalized_category"] for s in stats}
        assert "fee_calculation" in categories
        assert "line_item_accuracy" in categories
        for s in stats:
            assert s["percent"] == 50.0
            assert len(s["raw_reason_breakdown"]) == 1

    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_rejection_reason_filter(
        self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client,
    ):
        mock_cohort.return_value = [
            {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1},
            {"claim_id": 200, "AI_inv_process_status": 4, "dept_id": 1},
        ]
        mock_mongo.return_value = {}
        mock_canc.return_value = {
            100: {"reason_id": 7, "raw_reason": "Miscalculated Nested Line Items"},
            200: {"reason_id": 1, "raw_reason": "Incorrect line item description"},
        }
        mock_logs.return_value = {
            100: [{"log_text": "Invoice to Insurance - Cancelled"}],
            200: [{"log_text": "Invoice to Insurance - Cancelled"}],
        }

        response = test_client.get(
            "/api/ai-analytics/outcomes/rejection-reasons?reason_category=fee_calculation",
            headers=AUTH,
        )
        assert response.status_code == 200
        stats = response.json()
        assert [s["normalized_category"] for s in stats] == ["fee_calculation"]
        assert stats[0]["count"] == 1


# ---------------------------------------------------------------------------
# Invoice cohort endpoint
# ---------------------------------------------------------------------------

class TestInvoiceCohortRoute:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_invoice_cohort_pagination(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        # Create 5 claims
        mock_cohort.return_value = [
            {"claim_id": i, "AI_inv_process_status": 2, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "run_number": f"R{i}", "ai_business_updated_at": f"2026-01-{i:02d}T10:00:00"}
            for i in range(1, 6)
        ]
        mock_mongo.return_value = {}
        mock_canc.return_value = {}
        mock_logs.return_value = {}

        # Page 1 with page_size 2
        response = test_client.get("/api/ai-analytics/outcomes/invoices?page=1&page_size=2", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2
        assert len(data["invoices"]) == 2

        # Page 3 with page_size 2 → only 1 item
        response = test_client.get("/api/ai-analytics/outcomes/invoices?page=3&page_size=2", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert len(data["invoices"]) == 1


# ---------------------------------------------------------------------------
# Billability endpoint
# ---------------------------------------------------------------------------

class TestBillabilityRoute:
    @patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort")
    @patch("ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids", new_callable=AsyncMock)
    @patch("ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims")
    @patch("ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims")
    def test_billability_stats(self, mock_logs, mock_canc, mock_mongo, mock_cohort, test_client):
        mock_cohort.return_value = [
            {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-15T10:00:00"},
            {"claim_id": 200, "AI_inv_process_status": 4, "dept_id": 1,
             "department_name": "FD1", "department_state": "TX",
             "ai_business_updated_at": "2026-01-16T10:00:00"},
        ]
        mock_mongo.return_value = {
            100: {"claim_processing_status": "COMPLETED", "billing_category": "Motor Vehicle Accident"},
            200: {"claim_processing_status": "COMPLETED", "billing_category": None},
        }
        mock_canc.return_value = {}
        mock_logs.return_value = {}

        response = test_client.get("/api/ai-analytics/billability/stats", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["ai_records"] == 2
        assert data["billability_determined"] == 1
        assert data["billability_undetermined"] == 1
        assert data["billable"] == 1
        assert "Motor Vehicle Accident" in data["billing_category_distribution"]


# ---------------------------------------------------------------------------
# Date span validation
# ---------------------------------------------------------------------------

class TestDateSpanValidation:
    def test_date_span_exceeds_max(self, test_client):
        # 400 days apart
        response = test_client.get(
            "/api/ai-analytics/outcomes/summary?start_date=2025-01-01&end_date=2026-02-05"
        , headers=AUTH)
        assert response.status_code == 400
        assert "Date span" in response.json()["detail"]

    def test_valid_date_span(self, test_client):
        # Mock the cohort to return empty so we don't hit the DB
        with patch("ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort", return_value=[]):
            response = test_client.get(
                "/api/ai-analytics/outcomes/summary?start_date=2026-01-01&end_date=2026-01-31"
            , headers=AUTH)
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Shared route error handling (_handle_route_errors decorator)
# ---------------------------------------------------------------------------

class TestRouteErrorHandling:
    """The decorator must preserve a handler's own HTTPException status codes
    while converting ValueError to 400 and anything else to a generic 500."""

    def test_handler_raised_http_exception_keeps_its_status(self, test_client):
        # The trend handler validates `grain` itself and raises HTTPException(400)
        # from inside the decorated function body. A decorator that caught this
        # as a generic error would report 500 instead.
        response = test_client.get(
            "/api/ai-analytics/outcomes/trend?grain=bogus", headers=AUTH
        )
        assert response.status_code == 400
        assert "grain must be one of" in response.json()["detail"]

    def test_value_error_becomes_400_with_message(self, test_client):
        # Patch the service function in the routes module namespace — that's
        # the symbol the decorated handler actually calls, so the ValueError
        # propagates straight up to _handle_route_errors (the underlying
        # outcome_service swallows repo errors, so patching the repo wouldn't
        # exercise the decorator's ValueError branch).
        with patch(
            "ai_analytics_routes.get_outcome_summary",
            side_effect=ValueError("bad filter value"),
        ):
            response = test_client.get(
                "/api/ai-analytics/outcomes/summary", headers=AUTH
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "bad filter value"

    def test_unexpected_error_becomes_generic_500(self, test_client):
        with patch(
            "ai_analytics_routes.get_outcome_summary",
            side_effect=RuntimeError("connection reset by peer"),
        ):
            response = test_client.get(
                "/api/ai-analytics/outcomes/summary", headers=AUTH
            )
        assert response.status_code == 500
        # Internal details must not leak to the client
        assert response.json()["detail"] == "Internal server error"
        assert "connection reset" not in response.text

    def test_decorated_handlers_keep_their_query_params(self, test_client):
        """functools.wraps preserves __wrapped__, so FastAPI still resolves the
        original signature — query params and their validation stay intact."""
        # page_size has le=250; exceeding it must still be a 422 from FastAPI,
        # which proves the decorator did not erase the parameter metadata.
        response = test_client.get(
            "/api/ai-analytics/outcomes/invoices?page_size=9999", headers=AUTH
        )
        assert response.status_code == 422
