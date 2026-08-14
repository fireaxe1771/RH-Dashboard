"""Outcome analytics service — builds business-level AI success analytics.

Combines SQL + Mongo data at request time into normalized runtime views.
Follows the batch retrieval pattern from the plan (Section 28):
1. Query SQL cohort once
2. Extract all claim_ids
3. Query Mongo with $in
4. Query SQL cancellation details in one batch
5. Query SQL process logs in one batch
6. Normalize in memory
7. Aggregate
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    AiAnalyticsFilters,
    AiOutcomeSummary,
    AiOutcomeTrendPoint,
    AiRejectionReasonStat,
    AiRejectionReasonBreakdown,
    AiDepartmentOutcomeStat,
    AiPipelineStageStat,
    AiBillabilityStat,
    AiInvoiceListItem,
    AiInvoiceCohortResponse,
)
from . import sql_repository as sql_repo
from . import mongo_repository as mongo_repo
from . import projection_read_repository as projection_repo
from .normalization import (
    build_normalized_record,
    classify_business_outcome,
    is_terminal_outcome,
    calculate_release_rate,
    calculate_rejection_rate,
    classify_ai_execution_outcome,
    classify_writeback_status,
    calculate_retry_count,
    classify_billability,
    index_ai_records_by_claim_id,
    RELEASED_LOG_TEXT,
    CANCELLED_LOG_TEXT,
    AI_COMPLETED_STATUSES,
    AI_NOT_ENABLED_STATUSES,
)
from .reason_normalization import normalize_reason
from .cache import cached
from config import settings

logger = logging.getLogger(__name__)

MAX_DATE_SPAN_DAYS = 366


def _validate_date_span(start_date: Optional[str], end_date: Optional[str]) -> None:
    """Reject date ranges longer than MAX_DATE_SPAN_DAYS."""
    if not start_date or not end_date:
        return
    try:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        span = (end - start).days
        if span > MAX_DATE_SPAN_DAYS:
            raise ValueError(
                f"Date span exceeds maximum of {MAX_DATE_SPAN_DAYS} days (requested {span} days)."
            )
    except ValueError as e:
        if "Date span" in str(e):
            raise
        # If dates can't be parsed, let the SQL query handle it
        pass


async def _load_normalized_cohort(
    ai_db,
    filters: AiAnalyticsFilters,
) -> Tuple[List[Dict[str, Any]], Dict[str, str], bool]:
    """Load and normalize the full cohort for the given filters.

    Returns (normalized_records, source_status, data_complete).

    When ``settings.AI_ANALYTICS_USE_PROJECTION`` is true (Phase 9), step 2
    reads AI-side fields from the worker's ``ai_invoice_analytics``
    projection in the dashboard-owned Mongo instead of issuing a
    per-request ``$in`` against the operational RecoveryHub_AI Mongo.
    SQL steps (cohort, cancellations, process logs) are unchanged — the
    projection only caches the AI-Mongo side. When false, behaviour is
    identical to pre-Phase-9 (direct read from RecoveryHub_AI Mongo).
    """
    t0 = time.perf_counter()
    source_status: Dict[str, str] = {
        "recoveryhub_sql": "available",
        "recoveryhub_ai_mongo": "available",
    }
    data_complete = True

    # 1. Query SQL cohort
    t_sql = time.perf_counter()
    try:
        cohort_rows = sql_repo.get_ai_invoice_cohort(
            start_date=filters.start_date,
            end_date=filters.end_date,
            department_id=filters.department_id,
            date_basis=filters.date_basis,
        )
    except Exception as e:
        logger.error(f"SQL cohort query failed: {e}")
        source_status["recoveryhub_sql"] = "unavailable"
        return [], source_status, False
    logger.info(f"AI analytics: SQL cohort query took {time.perf_counter() - t_sql:.3f}s ({len(cohort_rows)} rows)")

    if not cohort_rows:
        return [], source_status, True

    claim_ids = [int(r["claim_id"]) for r in cohort_rows if r.get("claim_id")]

    # 2. Query AI-side data — projection (Phase 9) or direct Mongo read.
    t_ai = time.perf_counter()
    ai_records_by_claim: Dict[int, Dict[str, Any]] = {}
    try:
        if settings.AI_ANALYTICS_USE_PROJECTION:
            # Read from the dashboard-owned projection collection. The
            # adapter maps projection fields to the raw ai_line_items
            # shape so build_normalized_record works unchanged.
            from database import db_manager
            ai_records_by_claim = (
                await projection_repo.get_projection_records_for_claim_ids(
                    db_manager.db, claim_ids
                )
            )
            logger.info(
                "AI analytics: projection read took "
                f"{time.perf_counter() - t_ai:.3f}s "
                f"({len(ai_records_by_claim)} projections)"
            )
        else:
            ai_records_by_claim = await mongo_repo.get_ai_line_items_for_claim_ids(
                ai_db, claim_ids
            )
            logger.info(
                f"AI analytics: Mongo ai_line_items query took "
                f"{time.perf_counter() - t_ai:.3f}s "
                f"({len(ai_records_by_claim)} records)"
            )
    except Exception as e:
        logger.error(f"AI-side data query failed: {e}")
        source_status["recoveryhub_ai_mongo"] = "unavailable"
        data_complete = False

    # 3. Query SQL for cancellation details (batch)
    t_canc = time.perf_counter()
    cancellations_by_claim: Dict[int, Dict[str, Any]] = {}
    try:
        cancellations_by_claim = sql_repo.get_cancellation_details_for_claims(claim_ids)
    except Exception as e:
        logger.error(f"SQL cancellation details query failed: {e}")
        source_status["recoveryhub_sql"] = "partial"
        data_complete = False
    logger.info(f"AI analytics: SQL cancellation details took {time.perf_counter() - t_canc:.3f}s ({len(cancellations_by_claim)} records)")

    # 4. Query SQL for process logs (batch)
    t_logs = time.perf_counter()
    logs_by_claim: Dict[int, List[Dict[str, Any]]] = {}
    try:
        logs_by_claim = sql_repo.get_process_logs_for_claims(claim_ids)
    except Exception as e:
        logger.error(f"SQL process logs query failed: {e}")
        source_status["recoveryhub_sql"] = "partial"
        data_complete = False
    logger.info(f"AI analytics: SQL process logs took {time.perf_counter() - t_logs:.3f}s ({len(logs_by_claim)} claims)")

    # 5. Normalize in memory
    t_norm = time.perf_counter()
    normalized: List[Dict[str, Any]] = []
    for sql_row in cohort_rows:
        claim_id = int(sql_row["claim_id"])
        ai_record = ai_records_by_claim.get(claim_id)
        cancellation = cancellations_by_claim.get(claim_id)
        logs = logs_by_claim.get(claim_id, [])
        record = build_normalized_record(sql_row, ai_record, cancellation, logs)
        normalized.append(record)
    logger.info(f"AI analytics: Normalization took {time.perf_counter() - t_norm:.3f}s ({len(normalized)} records)")
    logger.info(f"AI analytics: Total _load_normalized_cohort took {time.perf_counter() - t0:.3f}s")

    return normalized, source_status, data_complete


def _apply_filters(
    records: List[Dict[str, Any]],
    filters: AiAnalyticsFilters,
) -> List[Dict[str, Any]]:
    """Apply post-normalization filters that can't be done in SQL."""
    result = []
    for r in records:
        if filters.business_outcome and r["business_outcome"] != filters.business_outcome:
            continue
        if filters.ai_processing_status and r.get("ai_processing_status") != filters.ai_processing_status:
            continue
        if filters.agent_execution_status and r.get("agent_execution_status") != filters.agent_execution_status:
            continue
        if filters.confidence_min is not None:
            if r.get("confidence") is None or r["confidence"] < filters.confidence_min:
                continue
        if filters.confidence_max is not None:
            if r.get("confidence") is None or r["confidence"] > filters.confidence_max:
                continue
        if filters.has_retry is not None:
            r_has_retry = r["retry_count"] > 0
            if r_has_retry != filters.has_retry:
                continue
        if filters.writeback_status and r.get("writeback_status") != filters.writeback_status:
            continue
        if filters.billing_category and r.get("billing_category") != filters.billing_category:
            continue
        if filters.reason_category and r.get("normalized_rejection_category") != filters.reason_category:
            continue
        result.append(r)
    return result


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@cached(ttl=60, func_name="get_outcome_summary")
async def get_outcome_summary(
    ai_db,
    filters: AiAnalyticsFilters,
) -> AiOutcomeSummary:
    """Build the executive KPI summary."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    total = len(records)
    released = sum(1 for r in records if r["business_outcome"] == "released")
    cancelled = sum(1 for r in records if r["business_outcome"] == "cancelled_rejected")
    pending = sum(1 for r in records if r["business_outcome"] == "pending")
    unknown = sum(1 for r in records if r["business_outcome"] == "unknown")
    terminal = released + cancelled

    # AI processing stats
    ai_completed = sum(1 for r in records if r.get("ai_processing_status") in AI_COMPLETED_STATUSES)
    ai_failed = sum(
        1 for r in records
        if classify_ai_execution_outcome(
            r.get("ai_processing_status"), r.get("agent_execution_status")
        ) == "failed"
    )
    ai_not_enabled = sum(
        1 for r in records
        if r.get("ai_processing_status") in AI_NOT_ENABLED_STATUSES
    )

    # Writeback stats
    writeback_success = sum(1 for r in records if r.get("writeback_status") == "success")
    writeback_failed = sum(
        1 for r in records if r.get("writeback_status") == "failed_or_not_saved"
    )

    # Confidence
    confidences = [r["confidence"] for r in records if r.get("confidence") is not None]
    avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None

    return AiOutcomeSummary(
        total_ai_invoices=total,
        released=released,
        cancelled_rejected=cancelled,
        pending=pending,
        unknown=unknown,
        terminal_count=terminal,
        business_release_rate=calculate_release_rate(released, cancelled) or 0.0,
        rejection_rate=calculate_rejection_rate(released, cancelled) or 0.0,
        ai_completed=ai_completed,
        ai_failed=ai_failed,
        ai_not_enabled=ai_not_enabled,
        writeback_success=writeback_success,
        writeback_failed=writeback_failed,
        avg_confidence=avg_confidence,
        source_status=source_status,
        data_complete=data_complete,
    )


# ---------------------------------------------------------------------------
# Funnel
# ---------------------------------------------------------------------------

@cached(ttl=60, func_name="get_outcome_funnel")
async def get_outcome_funnel(
    ai_db,
    filters: AiAnalyticsFilters,
) -> List[AiPipelineStageStat]:
    """Build the pipeline funnel."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    total = len(records)
    mongo_found = sum(1 for r in records if r["ai_record_state"] == "present")
    billability_determined = sum(
        1 for r in records
        if r.get("billing_category") is not None
    )
    ai_completed = sum(1 for r in records if r.get("ai_processing_status") in AI_COMPLETED_STATUSES)
    writeback_success = sum(1 for r in records if r.get("writeback_status") == "success")
    released = sum(1 for r in records if r["business_outcome"] == "released")
    cancelled = sum(1 for r in records if r["business_outcome"] == "cancelled_rejected")

    stages = [
        AiPipelineStageStat(
            stage="Entered RH AI workflow",
            count=total,
            description="Claims in AIInvoiceProcessRHTemp",
        ),
        AiPipelineStageStat(
            stage="Mongo AI record found",
            count=mongo_found,
            description="ai_line_items document exists",
        ),
        AiPipelineStageStat(
            stage="Billability determined",
            count=billability_determined,
            description="billing_category is not null",
        ),
        AiPipelineStageStat(
            stage="AI processing completed",
            count=ai_completed,
            description="claim_processing_status = COMPLETED",
        ),
        AiPipelineStageStat(
            stage="Line items saved to RH",
            count=writeback_success,
            description="line_items_save_to_rh_status = true",
        ),
        AiPipelineStageStat(
            stage="Business released",
            count=released,
            description="Invoice to Insurance - Released",
        ),
        AiPipelineStageStat(
            stage="Business cancelled/rejected",
            count=cancelled,
            description="Invoice to Insurance - Cancelled",
        ),
    ]

    return stages


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------

async def get_outcome_trend(
    ai_db,
    filters: AiAnalyticsFilters,
    grain: str = "day",
) -> List[AiOutcomeTrendPoint]:
    """Build trend over time."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    # Group by time period
    period_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    date_field_by_basis = {
        "business_status_date": "business_status_date",
        "claim_created_date": "claim_created_at",
        "ai_record_updated_date": "ai_business_updated_at",
    }
    date_field = date_field_by_basis.get(filters.date_basis)
    if date_field is None:
        raise ValueError(
            "date_basis must be one of: business_status_date, claim_created_date, ai_record_updated_date"
        )

    for r in records:
        date_str = r.get(date_field)
        if not date_str:
            continue
        try:
            dt = datetime.fromisoformat(date_str.replace("T", " ").split(".")[0])
        except (ValueError, TypeError):
            continue

        if grain == "week":
            # ISO week
            iso_year, iso_week, _ = dt.isocalendar()
            key = f"{iso_year}-W{iso_week:02d}"
        elif grain == "month":
            key = f"{dt.year}-{dt.month:02d}"
        else:
            key = dt.strftime("%Y-%m-%d")

        period_groups[key].append(r)

    points: List[AiOutcomeTrendPoint] = []
    for period in sorted(period_groups.keys()):
        group = period_groups[period]
        released = sum(1 for r in group if r["business_outcome"] == "released")
        rejected = sum(1 for r in group if r["business_outcome"] == "cancelled_rejected")
        pending = sum(1 for r in group if r["business_outcome"] == "pending")
        release_rate = calculate_release_rate(released, rejected)
        points.append(AiOutcomeTrendPoint(
            period=period,
            total=len(group),
            released=released,
            rejected=rejected,
            pending=pending,
            release_rate=release_rate,
        ))

    return points


# ---------------------------------------------------------------------------
# Rejection reasons
# ---------------------------------------------------------------------------

@cached(ttl=60, func_name="get_rejection_reasons")
async def get_rejection_reasons(
    ai_db,
    filters: AiAnalyticsFilters,
) -> List[AiRejectionReasonStat]:
    """Build rejection reason analytics with drill-down."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    # Only look at cancelled/rejected records
    rejected = [r for r in records if r["business_outcome"] == "cancelled_rejected"]

    # Group by normalized category
    category_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rejected:
        category = r.get("normalized_rejection_category") or "unknown"
        category_groups[category].append(r)

    total_rejected = len(rejected)
    stats: List[AiRejectionReasonStat] = []
    for category, group in sorted(category_groups.items(), key=lambda x: -len(x[1])):
        # Build raw reason breakdown
        raw_reason_counts: Counter = Counter()
        for r in group:
            raw_reason = r.get("raw_rejection_reason") or "Unknown"
            raw_reason_counts[raw_reason] += 1

        breakdown = [
            AiRejectionReasonBreakdown(raw_reason=reason, count=count)
            for reason, count in raw_reason_counts.most_common()
        ]

        percent = round(len(group) / total_rejected * 100, 2) if total_rejected > 0 else 0.0

        stats.append(AiRejectionReasonStat(
            normalized_category=category,
            count=len(group),
            percent=percent,
            raw_reason_breakdown=breakdown,
        ))

    return stats


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------

@cached(ttl=60, func_name="get_department_outcomes")
async def get_department_outcomes(
    ai_db,
    filters: AiAnalyticsFilters,
) -> List[AiDepartmentOutcomeStat]:
    """Build department comparison table."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    # Group by department
    dept_groups: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        dept_id = r.get("department_id")
        if dept_id is not None:
            dept_groups[int(dept_id)].append(r)

    stats: List[AiDepartmentOutcomeStat] = []
    for dept_id, group in dept_groups.items():
        released = sum(1 for r in group if r["business_outcome"] == "released")
        rejected = sum(1 for r in group if r["business_outcome"] == "cancelled_rejected")
        pending = sum(1 for r in group if r["business_outcome"] == "pending")

        ai_completed = sum(1 for r in group if r.get("ai_processing_status") in AI_COMPLETED_STATUSES)
        writeback_failed = sum(1 for r in group if r.get("writeback_status") == "failed_or_not_saved")

        confidences = [r["confidence"] for r in group if r.get("confidence") is not None]
        avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else None

        retry_count = sum(r.get("retry_count", 0) for r in group)
        human_intervention = sum(
            1 for r in group
            if r.get("business_user_id") is not None
        )

        first = group[0]
        stats.append(AiDepartmentOutcomeStat(
            department_id=dept_id,
            department_name=first.get("department_name"),
            state=first.get("department_state"),
            volume=len(group),
            released=released,
            rejected=rejected,
            pending=pending,
            release_rate=calculate_release_rate(released, rejected),
            ai_completion_rate=round(ai_completed / len(group) * 100, 2) if group else None,
            writeback_failure_rate=round(writeback_failed / len(group) * 100, 2) if group else None,
            avg_confidence=avg_conf,
            retry_count=retry_count,
            human_intervention_count=human_intervention,
        ))

    # Sort by volume descending
    stats.sort(key=lambda x: -x.volume)
    return stats


# ---------------------------------------------------------------------------
# Invoice cohort (drill-down)
# ---------------------------------------------------------------------------

async def get_invoice_cohort(
    ai_db,
    filters: AiAnalyticsFilters,
) -> AiInvoiceCohortResponse:
    """Return paged invoice cohort for drill-down."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    # Sort
    sort_by = filters.sort_by
    reverse = filters.sort_direction == "desc"
    if sort_by and records:
        records.sort(
            key=lambda r: (r.get(sort_by) is None, r.get(sort_by) or ""),
            reverse=reverse,
        )

    total = len(records)
    page = filters.page
    page_size = filters.page_size
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    page_records = records[start_idx:end_idx]

    invoices = [
        AiInvoiceListItem(
            claim_id=r["claim_id"],
            invoice_number=r.get("invoice_number"),
            department_id=r.get("department_id"),
            department_name=r.get("department_name"),
            run_number=r.get("run_number"),
            claim_created_at=r.get("claim_created_at"),
            ai_business_updated_at=r.get("ai_business_updated_at"),
            business_outcome=r["business_outcome"],
            raw_rejection_reason=r.get("raw_rejection_reason"),
            raw_rejection_description=r.get("raw_rejection_description"),
            normalized_rejection_category=r.get("normalized_rejection_category"),
            ai_processing_status=r.get("ai_processing_status"),
            agent_execution_status=r.get("agent_execution_status"),
            is_billable=r.get("is_billable"),
            billing_category=r.get("billing_category"),
            confidence=r.get("confidence"),
            writeback_status=r.get("writeback_status", "unknown"),
            retry_count=r.get("retry_count", 0),
            thread_id=r.get("thread_id"),
            ai_record_state=r.get("ai_record_state", "present"),
            business_record_state=r.get("business_record_state", "present"),
            invoice_total=r.get("invoice_total"),
            amount_invoiced=r.get("amount_invoiced"),
            processing_time_seconds=r.get("processing_time_seconds"),
        )
        for r in page_records
    ]

    return AiInvoiceCohortResponse(
        invoices=invoices,
        total_count=total,
        page=page,
        page_size=page_size,
        source_status=source_status,
        data_complete=data_complete,
    )


# ---------------------------------------------------------------------------
# Billability (Phase 4)
# ---------------------------------------------------------------------------

@cached(ttl=60, func_name="get_billability_stats")
async def get_billability_stats(
    ai_db,
    filters: AiAnalyticsFilters,
) -> AiBillabilityStat:
    """Build incident/billability evaluation metrics."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)

    # Only count records with AI data
    ai_records = [r for r in records if r.get("ai_record_state") == "present"]

    billability_determined = 0
    billability_undetermined = 0
    billable = 0
    not_billable = 0
    category_dist: Counter = Counter()

    for r in ai_records:
        billing_cat = r.get("billing_category")
        billab = classify_billability(
            billing_category=billing_cat,
            is_billable=r.get("is_billable"),
        )
        if billab["determined"]:
            billability_determined += 1
        if billab["undetermined"]:
            billability_undetermined += 1
        if billab["billable"]:
            billable += 1
        if billab["not_billable"]:
            not_billable += 1
        if billing_cat:
            category_dist[billing_cat] += 1

    return AiBillabilityStat(
        ai_records=len(ai_records),
        billability_determined=billability_determined,
        billability_undetermined=billability_undetermined,
        billable=billable,
        not_billable=not_billable,
        billing_category_distribution=dict(category_dist.most_common()),
    )
