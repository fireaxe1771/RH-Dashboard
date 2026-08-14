"""Tests for ai_analytics.projection_read_repository (Phase 9).

Feature under test: the adapter that maps the worker's
``ai_invoice_analytics`` projection documents to the raw
``ai_line_items`` field shape that ``build_normalized_record`` expects,
and the batch fetch function that reads projections by claim_id.

Failure prevented:
-- A field name mismatch between the projection and the raw ai_record
   shape would silently produce ``None`` for a field that has a real
   value in the source data. For example, the projection stores
   ``ai_processing_status`` but ``build_normalized_record`` reads
   ``claim_processing_status`` — without the rename, every record would
   show ``ai_processing_status=None`` and the status distribution
   dashboard would collapse to "unknown".
-- A missing projection for a claim that HAS an ai_line_items record
   would make the claim look like it has no AI data (``ai_record=None``),
   undercounting AI metrics. The adapter must handle this the same way
   the direct-read path does — by omitting the claim from the result.

Test level: unit (mapping) + integration (fetch with mongomock).
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_analytics.projection_read_repository import (
    projection_to_ai_record,
    get_projection_records_for_claim_ids,
)


# ---------------------------------------------------------------------------
# projection_to_ai_record — field mapping
# ---------------------------------------------------------------------------


class TestProjectionToAiRecord:
    """Verify the projection → ai_record field mapping is complete and correct."""

    def test_renames_ai_processing_status_to_claim_processing_status(self):
        """Projection's ``ai_processing_status`` → raw ``claim_processing_status``."""
        projection = {"ai_processing_status": "COMPLETED"}
        result = projection_to_ai_record(projection)
        assert result["claim_processing_status"] == "COMPLETED"

    def test_renames_agent_execution_status_to_agent_exec_status(self):
        """Projection's ``agent_execution_status`` → raw ``agent_exec_status``."""
        projection = {"agent_execution_status": "success"}
        result = projection_to_ai_record(projection)
        assert result["agent_exec_status"] == "success"

    def test_renames_ai_invoice_total_to_invoice_total(self):
        """Projection's ``ai_invoice_total`` → raw ``invoice_total``."""
        projection = {"ai_invoice_total": 1500.00}
        result = projection_to_ai_record(projection)
        assert result["invoice_total"] == 1500.00

    def test_renames_processing_duration_seconds_to_processing_time_seconds(self):
        """Projection's ``processing_duration_seconds`` → raw ``processing_time_seconds``."""
        projection = {"processing_duration_seconds": 42.5}
        result = projection_to_ai_record(projection)
        assert result["processing_time_seconds"] == 42.5

    def test_passthrough_fields_keep_same_name(self):
        """Fields with the same name in both shapes pass through unchanged."""
        projection = {
            "confidence_level": 85,
            "is_billable": True,
            "billing_category": "Fire Suppression",
            "line_items_save_to_rh_status": True,
            "retry_count": 2,
        }
        result = projection_to_ai_record(projection)
        assert result["confidence_level"] == 85
        assert result["is_billable"] is True
        assert result["billing_category"] == "Fire Suppression"
        assert result["line_items_save_to_rh_status"] is True
        assert result["retry_count"] == 2

    def test_retry_count_passthrough_not_silently_dropped(self):
        """``retry_count`` is 30% populated in production per the Phase 0
        audit and is stored in the projection (Section 9.6).

        Regression guard: a previous version of the adapter omitted
        ``retry_count`` from ``_PASSTHROUGH_FIELDS``, which silently
        dropped it and made every record report ``retry_count=0`` when
        the projection flag was on. ``calculate_retry_count`` reads
        ``ai_record.get("retry_count")`` directly — if the adapter
        doesn't carry it through, the has_retry filter and the
        retry_count column on the dashboard are wrong for 30% of records.
        """
        projection = {"retry_count": 3}
        result = projection_to_ai_record(projection)
        assert result["retry_count"] == 3

    def test_thread_id_and_retry_thread_id_are_none(self):
        """Fields not in the projection are explicitly None.

        Both are 0% populated in production per the Phase 0 audit, so
        None matches the direct-read path's behaviour.
        """
        projection = {}
        result = projection_to_ai_record(projection)
        assert result["thread_id"] is None
        assert result["retry_thread_id"] is None

    def test_empty_projection_produces_all_none_ai_record(self):
        """An empty projection dict produces a dict with all expected keys as None."""
        result = projection_to_ai_record({})
        expected_keys = {
            "confidence_level", "is_billable", "billing_category",
            "line_items_save_to_rh_status", "retry_count",
            "claim_processing_status", "agent_exec_status",
            "invoice_total", "processing_time_seconds",
            "thread_id", "retry_thread_id",
        }
        assert set(result.keys()) == expected_keys
        assert all(v is None for v in result.values())

    def test_full_projection_maps_all_fields(self):
        """A complete projection maps every field build_normalized_record reads."""
        projection = {
            "ai_processing_status": "COMPLETED",
            "agent_execution_status": "success",
            "confidence_level": 92,
            "is_billable": True,
            "billing_category": "Fire Suppression",
            "line_items_save_to_rh_status": True,
            "ai_invoice_total": 2500.00,
            "processing_duration_seconds": 18.3,
            "retry_count": 1,
        }
        result = projection_to_ai_record(projection)
        assert result == {
            "claim_processing_status": "COMPLETED",
            "agent_exec_status": "success",
            "confidence_level": 92,
            "is_billable": True,
            "billing_category": "Fire Suppression",
            "line_items_save_to_rh_status": True,
            "invoice_total": 2500.00,
            "processing_time_seconds": 18.3,
            "retry_count": 1,
            "thread_id": None,
            "retry_thread_id": None,
        }


# ---------------------------------------------------------------------------
# get_projection_records_for_claim_ids — batch fetch
# ---------------------------------------------------------------------------


class TestGetProjectionRecordsForClaimIds:
    """Verify the batch fetch returns adapted dicts keyed by claim_id."""

    @pytest.mark.asyncio
    async def test_empty_claim_ids_returns_empty_dict(self):
        """No claim_ids → no query, empty result."""
        result = await get_projection_records_for_claim_ids(object(), [])
        assert result == {}

    @pytest.mark.asyncio
    async def test_fetches_projections_by_id(self, mock_mongo_db):
        """Projections are fetched by ``_id`` (which is the integer claim_id)."""
        from ai_analytics_worker.config import worker_config

        collection = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]
        await collection.insert_one({
            "_id": 100,
            "ai_processing_status": "COMPLETED",
            "agent_execution_status": "success",
            "confidence_level": 90,
            "retry_count": 2,
        })
        await collection.insert_one({
            "_id": 200,
            "ai_processing_status": "INITIATED",
            "agent_execution_status": "pending",
            "confidence_level": None,
            "retry_count": 0,
        })

        result = await get_projection_records_for_claim_ids(
            mock_mongo_db, [100, 200, 999]
        )

        # 999 has no projection — absent from result, same as direct-read.
        assert set(result.keys()) == {100, 200}
        assert result[100]["claim_processing_status"] == "COMPLETED"
        assert result[100]["retry_count"] == 2
        assert result[200]["claim_processing_status"] == "INITIATED"
        assert result[200]["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_missing_projection_is_absent_not_none(self, mock_mongo_db):
        """A claim with no projection is absent from the result dict.

        This matches the direct-read path where
        ``get_ai_line_items_for_claim_ids`` omits claims with no AI
        record. ``build_normalized_record`` then receives
        ``ai_record=None`` and handles it gracefully.
        """
        result = await get_projection_records_for_claim_ids(
            mock_mongo_db, [99999]
        )
        assert 99999 not in result
        assert result == {}

    @pytest.mark.asyncio
    async def test_propagates_database_errors(self):
        """A database failure propagates so _load_normalized_cohort can mark it."""
        from unittest.mock import MagicMock

        failing_db = MagicMock()
        failing_collection = MagicMock()
        failing_collection.find.side_effect = RuntimeError("connection lost")
        failing_db.__getitem__ = MagicMock(return_value=failing_collection)

        with pytest.raises(RuntimeError, match="connection lost"):
            await get_projection_records_for_claim_ids(failing_db, [100])


# ---------------------------------------------------------------------------
# _load_normalized_cohort — flag-gated path selection
# ---------------------------------------------------------------------------


class TestLoadNormalizedCohortFlagGating:
    """Verify _load_normalized_cohort uses the projection when the flag is on."""

    @pytest.mark.asyncio
    async def test_flag_off_uses_direct_mongo_read(self, monkeypatch):
        """When AI_ANALYTICS_USE_PROJECTION is false, the direct Mongo path is used."""
        from ai_analytics.outcome_service import _load_normalized_cohort
        from ai_analytics.models import AiAnalyticsFilters
        from config import settings

        monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", False)

        with patch(
            "ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort"
        ) as mock_cohort, patch(
            "ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids",
            new_callable=AsyncMock,
        ) as mock_mongo, patch(
            "ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims"
        ) as mock_canc, patch(
            "ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims"
        ) as mock_logs:
            # Non-empty cohort so the function proceeds past the early
            # return to the AI-side query step.
            mock_cohort.return_value = [
                {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
                 "department_name": "FD1", "department_state": "TX",
                 "ai_business_updated_at": "2026-01-15T10:00:00"},
            ]
            mock_mongo.return_value = {}
            mock_canc.return_value = {}
            mock_logs.return_value = {}

            filters = AiAnalyticsFilters()
            records, source_status, data_complete = (
                await _load_normalized_cohort(object(), filters)
            )

            assert mock_mongo.called
            assert len(records) == 1

    @pytest.mark.asyncio
    async def test_flag_on_uses_projection_read(self, monkeypatch, mock_mongo_db):
        """When AI_ANALYTICS_USE_PROJECTION is true, the projection path is used."""
        from ai_analytics.outcome_service import _load_normalized_cohort
        from ai_analytics.models import AiAnalyticsFilters
        from ai_analytics_worker.config import worker_config
        from config import settings
        from database import db_manager

        monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)
        # The conftest sets db_manager.db to mock_mongo_db, but we need
        # to ensure it's the same instance the function will import.
        monkeypatch.setattr(db_manager, "db", mock_mongo_db)

        # Seed a projection into the dashboard-owned Mongo.
        collection = mock_mongo_db[worker_config.PROJECTIONS_COLLECTION]
        await collection.insert_one({
            "_id": 100,
            "ai_processing_status": "COMPLETED",
            "agent_execution_status": "success",
            "confidence_level": 90,
            "is_billable": True,
            "billing_category": "Fire Suppression",
            "line_items_save_to_rh_status": True,
            "ai_invoice_total": 1500.00,
            "processing_duration_seconds": 15.5,
            "retry_count": 2,
        })

        with patch(
            "ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort"
        ) as mock_cohort, patch(
            "ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids",
            new_callable=AsyncMock,
        ) as mock_mongo, patch(
            "ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims"
        ) as mock_canc, patch(
            "ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims"
        ) as mock_logs:
            mock_cohort.return_value = [
                {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
                 "department_name": "FD1", "department_state": "TX",
                 "ai_business_updated_at": "2026-01-15T10:00:00"},
            ]
            mock_canc.return_value = {}
            mock_logs.return_value = {
                100: [{"log_text": "Invoice to Insurance - Released",
                        "user_id": 7486, "user_type_id": 2}],
            }

            filters = AiAnalyticsFilters()
            records, source_status, data_complete = (
                await _load_normalized_cohort(mock_mongo_db, filters)
            )

            # Direct Mongo read must NOT have been called.
            assert not mock_mongo.called

            # The projection data flowed through build_normalized_record.
            assert len(records) == 1
            record = records[0]
            assert record["claim_id"] == 100
            assert record["ai_processing_status"] == "COMPLETED"
            assert record["agent_execution_status"] == "success"
            assert record["confidence"] == 90
            assert record["is_billable"] is True
            assert record["billing_category"] == "Fire Suppression"
            assert record["invoice_total"] == 1500.00
            assert record["processing_time_seconds"] == 15.5
            assert record["retry_count"] == 2
            # business_outcome comes from the SQL join, not the projection.
            assert record["business_outcome"] == "released"

    @pytest.mark.asyncio
    async def test_projection_missing_claim_treated_as_no_ai_record(
        self, monkeypatch, mock_mongo_db
    ):
        """A claim with a SQL row but no projection gets ai_record=None.

        This matches the direct-read path: if there's no ai_line_items
        document for a claim, build_normalized_record receives None and
        sets ai_record_state="missing".
        """
        from ai_analytics.outcome_service import _load_normalized_cohort
        from ai_analytics.models import AiAnalyticsFilters
        from config import settings
        from database import db_manager

        monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)
        monkeypatch.setattr(db_manager, "db", mock_mongo_db)

        # No projections seeded — the projection collection is empty.

        with patch(
            "ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort"
        ) as mock_cohort, patch(
            "ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids",
            new_callable=AsyncMock,
        ) as mock_mongo, patch(
            "ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims"
        ) as mock_canc, patch(
            "ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims"
        ) as mock_logs:
            mock_cohort.return_value = [
                {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
                 "department_name": "FD1", "department_state": "TX",
                 "ai_business_updated_at": "2026-01-15T10:00:00"},
            ]
            mock_canc.return_value = {}
            mock_logs.return_value = {}

            filters = AiAnalyticsFilters()
            records, _, _ = await _load_normalized_cohort(
                mock_mongo_db, filters
            )

            assert len(records) == 1
            assert records[0]["ai_record_state"] == "missing"
            assert records[0]["ai_processing_status"] is None

    @pytest.mark.asyncio
    async def test_projection_read_failure_marks_data_incomplete(
        self, monkeypatch
    ):
        """If the projection query fails, source_status reflects it."""
        from ai_analytics.outcome_service import _load_normalized_cohort
        from ai_analytics.models import AiAnalyticsFilters
        from config import settings
        from database import db_manager

        monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)

        # Make db_manager.db raise on collection access.
        failing_db = MagicMock()
        failing_db.__getitem__ = MagicMock(
            side_effect=RuntimeError("db is down")
        )
        monkeypatch.setattr(db_manager, "db", failing_db)

        with patch(
            "ai_analytics.outcome_service.sql_repo.get_ai_invoice_cohort"
        ) as mock_cohort, patch(
            "ai_analytics.outcome_service.mongo_repo.get_ai_line_items_for_claim_ids",
            new_callable=AsyncMock,
        ) as mock_mongo, patch(
            "ai_analytics.outcome_service.sql_repo.get_cancellation_details_for_claims"
        ) as mock_canc, patch(
            "ai_analytics.outcome_service.sql_repo.get_process_logs_for_claims"
        ) as mock_logs:
            mock_cohort.return_value = [
                {"claim_id": 100, "AI_inv_process_status": 4, "dept_id": 1,
                 "department_name": "FD1", "department_state": "TX",
                 "ai_business_updated_at": "2026-01-15T10:00:00"},
            ]
            mock_canc.return_value = {}
            mock_logs.return_value = {}

            filters = AiAnalyticsFilters()
            records, source_status, data_complete = (
                await _load_normalized_cohort(object(), filters)
            )

            assert source_status["recoveryhub_ai_mongo"] == "unavailable"
            assert data_complete is False
