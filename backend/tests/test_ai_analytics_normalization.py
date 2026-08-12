"""Unit tests for ai_analytics.normalization and reason_normalization.

Tests the core business logic: status classification, release rate,
reason normalization, retry detection, writeback normalization,
confidence bucketing, and billability classification.
"""

import pytest
from unittest.mock import MagicMock

from ai_analytics.normalization import (
    classify_business_outcome,
    is_terminal_outcome,
    calculate_release_rate,
    calculate_rejection_rate,
    classify_ai_execution_outcome,
    classify_writeback_status,
    calculate_retry_count,
    has_retry,
    calculate_processing_duration,
    calculate_duration_percentiles,
    confidence_bucket,
    detect_human_intervention,
    classify_billability,
    build_normalized_record,
    index_ai_records_by_claim_id,
    RELEASED_LOG_TEXT,
    CANCELLED_LOG_TEXT,
    STATUS_TERMINAL,
    STATUS_PROCESSING,
    STATUS_ACTIVE_REVIEW,
)
from ai_analytics.reason_normalization import (
    normalize_reason,
    REASON_ID_TO_CATEGORY,
    NORMALIZED_CATEGORIES,
)


# ---------------------------------------------------------------------------
# Business outcome classification
# ---------------------------------------------------------------------------

class TestClassifyBusinessOutcome:
    def test_released_with_log(self):
        result = classify_business_outcome(
            ai_inv_process_status=4,
            has_released_log=True,
        )
        assert result == "released"

    def test_cancelled_with_cancellation_record(self):
        result = classify_business_outcome(
            ai_inv_process_status=4,
            has_cancellation_record=True,
        )
        assert result == "cancelled_rejected"

    def test_cancelled_with_cancelled_log(self):
        result = classify_business_outcome(
            ai_inv_process_status=4,
            has_cancelled_log=True,
        )
        assert result == "cancelled_rejected"

    def test_cancelled_takes_priority_over_released(self):
        """If both released and cancelled logs exist, cancelled wins."""
        result = classify_business_outcome(
            ai_inv_process_status=4,
            has_released_log=True,
            has_cancelled_log=True,
        )
        assert result == "cancelled_rejected"

    def test_pending_status_2(self):
        result = classify_business_outcome(
            ai_inv_process_status=2,
        )
        assert result == "pending"

    def test_pending_status_9(self):
        result = classify_business_outcome(
            ai_inv_process_status=9,
        )
        assert result == "pending"

    def test_pending_status_1(self):
        result = classify_business_outcome(
            ai_inv_process_status=1,
        )
        assert result == "pending"

    def test_status_4_no_logs_is_unknown(self):
        result = classify_business_outcome(
            ai_inv_process_status=4,
        )
        assert result == "unknown"

    def test_status_7_treated_as_released(self):
        result = classify_business_outcome(
            ai_inv_process_status=7,
        )
        assert result == "released"

    def test_none_status_is_unknown(self):
        result = classify_business_outcome(
            ai_inv_process_status=None,
        )
        assert result == "unknown"


class TestIsTerminalOutcome:
    def test_released_is_terminal(self):
        assert is_terminal_outcome("released") is True

    def test_cancelled_is_terminal(self):
        assert is_terminal_outcome("cancelled_rejected") is True

    def test_pending_is_not_terminal(self):
        assert is_terminal_outcome("pending") is False

    def test_unknown_is_not_terminal(self):
        assert is_terminal_outcome("unknown") is False


# ---------------------------------------------------------------------------
# Release / rejection rate
# ---------------------------------------------------------------------------

class TestReleaseRate:
    def test_basic_release_rate(self):
        assert calculate_release_rate(800, 100) == 88.89

    def test_all_released(self):
        assert calculate_release_rate(100, 0) == 100.0

    def test_all_cancelled(self):
        assert calculate_release_rate(0, 100) == 0.0

    def test_zero_denominator_returns_none(self):
        assert calculate_release_rate(0, 0) is None

    def test_pending_excluded_from_denominator(self):
        """Pending should not affect the rate — only terminal outcomes count."""
        rate = calculate_release_rate(800, 100)
        # Even if there are 100 pending, the rate should be the same
        assert rate == 88.89


class TestRejectionRate:
    def test_basic_rejection_rate(self):
        assert calculate_rejection_rate(800, 100) == 11.11

    def test_all_cancelled(self):
        assert calculate_rejection_rate(0, 100) == 100.0

    def test_zero_denominator_returns_none(self):
        assert calculate_rejection_rate(0, 0) is None


# ---------------------------------------------------------------------------
# AI execution outcome
# ---------------------------------------------------------------------------

class TestClassifyAiExecutionOutcome:
    def test_completed(self):
        assert classify_ai_execution_outcome("COMPLETED", "success") == "completed"

    def test_completed_with_issues(self):
        assert classify_ai_execution_outcome("COMPLETED", "completed_with_issues") == "completed"

    def test_completed_but_agent_error(self):
        assert classify_ai_execution_outcome("COMPLETED", "error") == "failed"

    def test_not_enabled(self):
        assert classify_ai_execution_outcome("BILLING_LEVEL_NOT_ENABLED", "success") == "not_enabled"

    def test_in_progress(self):
        assert classify_ai_execution_outcome("INITIATED", "in_progress") == "in_progress"

    def test_error_status(self):
        assert classify_ai_execution_outcome("ERROR", "error") == "failed"

    def test_unknown(self):
        assert classify_ai_execution_outcome(None, None) == "unknown"


# ---------------------------------------------------------------------------
# Writeback normalization
# ---------------------------------------------------------------------------

class TestClassifyWritebackStatus:
    def test_success(self):
        assert classify_writeback_status(True, "COMPLETED") == "success"

    def test_failed(self):
        assert classify_writeback_status(False, "COMPLETED") == "failed_or_not_saved"

    def test_not_required_when_not_enabled(self):
        assert classify_writeback_status(False, "BILLING_LEVEL_NOT_ENABLED") == "not_required"

    def test_pending_when_in_progress(self):
        assert classify_writeback_status(False, "IN_PROGRESS") == "pending"

    def test_unknown_when_none(self):
        assert classify_writeback_status(None, None) == "unknown"


# ---------------------------------------------------------------------------
# Retry detection
# ---------------------------------------------------------------------------

class TestRetryDetection:
    def test_no_retry(self):
        assert calculate_retry_count(ai_record={"retry_count": 0}) == 0

    def test_retry_count_from_field(self):
        assert calculate_retry_count(ai_record={"retry_count": 3}) == 3

    def test_retry_from_retry_thread_id(self):
        assert calculate_retry_count(retry_thread_id="thread_123") == 1

    def test_retry_from_agent_status(self):
        assert calculate_retry_count(agent_exec_status="retry") == 1

    def test_retry_count_takes_max(self):
        result = calculate_retry_count(
            ai_record={"retry_count": 2},
            retry_thread_id="thread_456",
            agent_exec_status="retry",
        )
        assert result == 2  # max of 2, 1, 1

    def test_has_retry_true(self):
        assert has_retry(ai_record={"retry_count": 1}) is True

    def test_has_retry_false(self):
        assert has_retry(ai_record={"retry_count": 0}) is False


# ---------------------------------------------------------------------------
# Processing duration
# ---------------------------------------------------------------------------

class TestProcessingDuration:
    def test_from_explicit_param(self):
        assert calculate_processing_duration(processing_time_seconds=42.5) == 42.5

    def test_from_ai_record(self):
        assert calculate_processing_duration(ai_record={"processing_time_seconds": 30.0}) == 30.0

    def test_none_when_missing(self):
        assert calculate_processing_duration() is None

    def test_none_when_invalid(self):
        assert calculate_processing_duration(ai_record={"processing_time_seconds": "invalid"}) is None


class TestDurationPercentiles:
    def test_empty_list(self):
        result = calculate_duration_percentiles([])
        assert result == {"avg": None, "p50": None, "p90": None, "p95": None}

    def test_single_value(self):
        result = calculate_duration_percentiles([10.0])
        assert result["avg"] == 10.0
        assert result["p50"] == 10.0

    def test_multiple_values(self):
        durations = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        result = calculate_duration_percentiles(durations)
        assert result["avg"] == 5.5
        # percentile(50) -> idx = round(50/100 * 9) = round(4.5) = 4 (banker's) -> durations[4] = 5
        assert result["p50"] == 5
        assert result["p95"] is not None


# ---------------------------------------------------------------------------
# Confidence bucketing
# ---------------------------------------------------------------------------

class TestConfidenceBucket:
    def test_bucket_0(self):
        assert confidence_bucket(0) == "0-49"

    def test_bucket_49(self):
        assert confidence_bucket(49) == "0-49"

    def test_bucket_50(self):
        assert confidence_bucket(50) == "50-69"

    def test_bucket_69(self):
        assert confidence_bucket(69) == "50-69"

    def test_bucket_70(self):
        assert confidence_bucket(70) == "70-79"

    def test_bucket_79(self):
        assert confidence_bucket(79) == "70-79"

    def test_bucket_80(self):
        assert confidence_bucket(80) == "80-89"

    def test_bucket_89(self):
        assert confidence_bucket(89) == "80-89"

    def test_bucket_90(self):
        assert confidence_bucket(90) == "90-100"

    def test_bucket_100(self):
        assert confidence_bucket(100) == "90-100"

    def test_none_is_unknown(self):
        assert confidence_bucket(None) == "unknown"

    def test_invalid_is_unknown(self):
        assert confidence_bucket("invalid") == "unknown"


# ---------------------------------------------------------------------------
# Reason normalization
# ---------------------------------------------------------------------------

class TestReasonNormalization:
    def test_known_reason_id(self):
        result = normalize_reason(7, "Miscalculated Nested Line Items")
        assert result["normalized_category"] == "fee_calculation"
        assert result["raw_reason"] == "Miscalculated Nested Line Items"

    def test_unknown_reason_id(self):
        result = normalize_reason(999, "Some unknown reason")
        # Falls back to fuzzy matching
        assert result["normalized_category"] == "unknown"

    def test_none_reason_id_with_fuzzy_match(self):
        result = normalize_reason(None, "Wrong Level Selected")
        assert result["normalized_category"] == "level_classification"

    def test_none_reason_id_no_match(self):
        result = normalize_reason(None, "Completely unique reason")
        assert result["normalized_category"] == "unknown"

    def test_preserves_description(self):
        result = normalize_reason(7, "Miscalculated Nested Line Items", "User entered wrong qty")
        assert result["raw_description"] == "User entered wrong qty"

    def test_all_reason_ids_mapped(self):
        """Every reason_id in the Phase 0 inventory has a mapping."""
        # Reason IDs 1-17 from the Phase 0 data contract
        for rid in range(1, 18):
            assert rid in REASON_ID_TO_CATEGORY, f"reason_id {rid} not mapped"

    def test_all_categories_in_normalied_set(self):
        expected = {
            "fee_calculation", "line_item_accuracy", "level_classification",
            "time_on_scene", "department_data_issue", "workflow_update",
            "test_removal", "nested_line_item_canceled", "command_error",
        }
        assert expected.issubset(set(NORMALIZED_CATEGORIES))


# ---------------------------------------------------------------------------
# Human intervention
# ---------------------------------------------------------------------------

class TestDetectHumanIntervention:
    def test_no_evidence(self):
        result = detect_human_intervention(
            process_logs=[],
            ai_line_items=[],
            final_line_items=[],
            review_msg=None,
            retry_count=0,
        )
        assert result == "no_evidence_of_human_intervention"

    def test_confirmed_via_human_user_log(self):
        """A process log from a non-system user is strong evidence."""
        result = detect_human_intervention(
            process_logs=[{"user_type_id": 2, "log_text": "Manual edit"}],
        )
        assert result == "confirmed_human_intervention"

    def test_likely_via_line_item_diff(self):
        result = detect_human_intervention(
            ai_line_items=[{"item": "Level 1", "rate": 100, "quantity": 1}],
            final_line_items=[{"item": "Level 2", "rate": 200, "quantity": 1}],
        )
        assert result == "likely_human_intervention"

    def test_confirmed_via_multiple_evidence(self):
        result = detect_human_intervention(
            ai_line_items=[{"item": "Level 1"}],
            final_line_items=[{"item": "Level 2"}],
            review_msg="Incorrect calculation, needs correction",
            retry_count=1,
        )
        assert result == "confirmed_human_intervention"


# ---------------------------------------------------------------------------
# Billability
# ---------------------------------------------------------------------------

class TestClassifyBillability:
    def test_determined_via_billing_category(self):
        result = classify_billability(billing_category="Motor Vehicle Accident")
        assert result["determined"] is True
        assert result["undetermined"] is False
        assert result["billable"] is True

    def test_undetermined_when_no_category(self):
        result = classify_billability(billing_category=None)
        assert result["determined"] is False
        assert result["undetermined"] is True

    def test_uses_is_billable_when_populated(self):
        result = classify_billability(
            billing_category=None,
            is_billable=True,
            is_billable_not_determined=False,
        )
        assert result["determined"] is True
        assert result["billable"] is True

    def test_not_billable_when_is_billable_false(self):
        result = classify_billability(
            billing_category=None,
            is_billable=False,
        )
        assert result["not_billable"] is True


# ---------------------------------------------------------------------------
# build_normalized_record
# ---------------------------------------------------------------------------

class TestBuildNormalizedRecord:
    def test_basic_released_record(self):
        sql_row = {
            "claim_id": 12345,
            "ai_inv_process_status": 4,
            "dept_id": 100,
            "department_name": "Test FD",
            "department_state": "TX",
            "run_number": "RUN-001",
            "invoice_number": "INV-001",
            "amount_invoiced": 500.0,
            "claim_created_at": "2026-01-01",
            "ai_business_updated_at": "2026-01-15T10:00:00",
        }
        ai_record = {
            "claim_processing_status": "COMPLETED",
            "agent_exec_status": "success",
            "confidence_level": 85,
            "line_items_save_to_rh_status": True,
            "billing_category": "Motor Vehicle Accident",
            "invoice_total": 500.0,
            "retry_count": 0,
            "processing_time_seconds": 15.5,
        }
        logs = [
            {"log_text": "Line Item Created", "user_id": 10499, "user_type_id": 1},
            {"log_text": RELEASED_LOG_TEXT, "user_id": 7486, "user_type_id": 2},
        ]

        result = build_normalized_record(sql_row, ai_record, None, logs)

        assert result["claim_id"] == 12345
        assert result["business_outcome"] == "released"
        assert result["ai_processing_status"] == "COMPLETED"
        assert result["confidence"] == 85
        assert result["writeback_status"] == "success"
        assert result["retry_count"] == 0
        assert result["billing_category"] == "Motor Vehicle Accident"
        assert result["processing_time_seconds"] == 15.5

    def test_cancelled_with_reason(self):
        sql_row = {
            "claim_id": 67890,
            "ai_inv_process_status": 4,
            "dept_id": 200,
            "department_name": "Other FD",
        }
        cancellation = {
            "reason_id": 7,
            "raw_reason": "Miscalculated Nested Line Items",
            "reason_descr": "Wrong qty",
        }
        logs = [
            {"log_text": CANCELLED_LOG_TEXT, "user_id": 7486, "user_type_id": 2},
        ]

        result = build_normalized_record(sql_row, None, cancellation, logs)

        assert result["business_outcome"] == "cancelled_rejected"
        assert result["raw_rejection_reason"] == "Miscalculated Nested Line Items"
        assert result["normalized_rejection_category"] == "fee_calculation"
        assert result["ai_record_state"] == "missing"

    def test_pending_status_2(self):
        sql_row = {
            "claim_id": 11111,
            "ai_inv_process_status": 2,
        }
        result = build_normalized_record(sql_row, None, None, None)
        assert result["business_outcome"] == "pending"


# ---------------------------------------------------------------------------
# index_ai_records_by_claim_id
# ---------------------------------------------------------------------------

class TestIndexAiRecords:
    def test_basic_indexing(self):
        records = [
            {"claim_id": 100, "updated_at": "2026-01-01"},
            {"claim_id": 200, "updated_at": "2026-01-02"},
        ]
        result = index_ai_records_by_claim_id(records)
        assert len(result) == 2
        assert 100 in result
        assert 200 in result

    def test_duplicate_keeps_most_recent(self):
        records = [
            {"claim_id": 100, "updated_at": "2026-01-01T10:00:00"},
            {"claim_id": 100, "updated_at": "2026-01-02T10:00:00"},
        ]
        result = index_ai_records_by_claim_id(records)
        assert len(result) == 1
        assert result[100]["updated_at"] == "2026-01-02T10:00:00"

    def test_string_claim_id_normalized(self):
        records = [{"claim_id": "100", "updated_at": "2026-01-01"}]
        result = index_ai_records_by_claim_id(records)
        assert 100 in result

    def test_empty_list(self):
        assert index_ai_records_by_claim_id([]) == {}
