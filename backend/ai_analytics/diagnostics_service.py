"""Diagnostics analytics service — AI execution health and reliability.

Provides visibility into AI processing status, confidence distribution,
retry patterns, writeback failures, and agent execution metrics.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    AiAnalyticsFilters,
    AiDiagnosticsSummary,
    AiConfidenceBucketStat,
    AiAgentStat,
)
from . import sql_repository as sql_repo
from . import mongo_repository as mongo_repo
from .normalization import (
    classify_writeback_status,
    calculate_retry_count,
    calculate_processing_duration,
    calculate_duration_percentiles,
    confidence_bucket,
    classify_ai_execution_outcome,
    AI_COMPLETED_STATUSES,
    AI_NOT_ENABLED_STATUSES,
)
from .outcome_service import _load_normalized_cohort, _apply_filters, _validate_date_span

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

async def get_diagnostics_summary(
    ai_db,
    filters: AiAnalyticsFilters,
) -> AiDiagnosticsSummary:
    """Build the AI diagnostics health summary."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, source_status, data_complete = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    total = len(records)
    completed = sum(1 for r in records if r.get("ai_processing_status") in AI_COMPLETED_STATUSES)
    errors = sum(
        1 for r in records
        if classify_ai_execution_outcome(
            r.get("ai_processing_status"), r.get("agent_execution_status")
        ) == "failed"
    )
    retries = sum(1 for r in records if r.get("retry_count", 0) > 0)
    retry_success = sum(
        1 for r in records
        if r.get("retry_count", 0) > 0
        and r.get("ai_processing_status") in AI_COMPLETED_STATUSES
    )
    low_confidence = sum(
        1 for r in records
        if r.get("confidence") is not None and r["confidence"] < 50
    )
    writeback_failures = sum(
        1 for r in records if r.get("writeback_status") == "failed_or_not_saved"
    )

    # Duration percentiles
    durations = [
        calculate_processing_duration(ai_record={"processing_time_seconds": r.get("processing_time_seconds")})
        for r in records
        if r.get("processing_time_seconds") is not None
    ]
    duration_stats = calculate_duration_percentiles(durations)

    return AiDiagnosticsSummary(
        ai_runs=total,
        completed=completed,
        errors=errors,
        retries=retries,
        retry_success=retry_success,
        low_confidence=low_confidence,
        writeback_failures=writeback_failures,
        avg_duration=duration_stats["avg"],
        p50_duration=duration_stats["p50"],
        p90_duration=duration_stats["p90"],
        p95_duration=duration_stats["p95"],
        source_status=source_status,
        data_complete=data_complete,
    )


# ---------------------------------------------------------------------------
# Status distribution
# ---------------------------------------------------------------------------

async def get_status_distribution(
    ai_db,
    filters: AiAnalyticsFilters,
) -> List[Dict[str, Any]]:
    """Distribution of claim_processing_status and agent_exec_status."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, _, _ = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    # claim_processing_status distribution
    cps_counts: Counter = Counter()
    aes_counts: Counter = Counter()
    for r in records:
        cps = r.get("ai_processing_status") or "unknown"
        aes = r.get("agent_execution_status") or "unknown"
        cps_counts[cps] += 1
        aes_counts[aes] += 1

    return [
        {
            "dimension": "claim_processing_status",
            "value": status,
            "count": count,
        }
        for status, count in cps_counts.most_common()
    ] + [
        {
            "dimension": "agent_exec_status",
            "value": status,
            "count": count,
        }
        for status, count in aes_counts.most_common()
    ]


# ---------------------------------------------------------------------------
# Confidence distribution
# ---------------------------------------------------------------------------

async def get_confidence_distribution(
    ai_db,
    filters: AiAnalyticsFilters,
) -> List[AiConfidenceBucketStat]:
    """Confidence bucket distribution with outcome breakdown."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, _, _ = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    # Group by confidence bucket
    buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {
        "count": 0, "released": 0, "rejected": 0, "pending": 0,
    })

    for r in records:
        bucket = confidence_bucket(r.get("confidence"))
        buckets[bucket]["count"] += 1
        outcome = r.get("business_outcome", "unknown")
        if outcome == "released":
            buckets[bucket]["released"] += 1
        elif outcome == "cancelled_rejected":
            buckets[bucket]["rejected"] += 1
        elif outcome == "pending":
            buckets[bucket]["pending"] += 1

    # Ordered buckets
    bucket_order = ["0-49", "50-69", "70-79", "80-89", "90-100", "unknown"]
    stats: List[AiConfidenceBucketStat] = []
    for bucket_name in bucket_order:
        data = buckets.get(bucket_name)
        if data is None or data["count"] == 0:
            continue
        terminal = data["released"] + data["rejected"]
        release_rate = round(data["released"] / terminal * 100, 2) if terminal > 0 else None
        stats.append(AiConfidenceBucketStat(
            bucket=bucket_name,
            count=data["count"],
            released=data["released"],
            rejected=data["rejected"],
            pending=data["pending"],
            release_rate=release_rate,
        ))

    return stats


# ---------------------------------------------------------------------------
# Retry analysis
# ---------------------------------------------------------------------------

async def get_retry_analysis(
    ai_db,
    filters: AiAnalyticsFilters,
) -> Dict[str, Any]:
    """Retry pattern analysis."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, _, _ = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    total = len(records)
    records_with_retries = [r for r in records if r.get("retry_count", 0) > 0]
    retry_count = len(records_with_retries)

    # Retry count distribution
    retry_dist: Counter = Counter()
    for r in records_with_retries:
        retry_dist[r["retry_count"]] += 1

    # Outcome distribution for retried records
    retried_outcomes: Counter = Counter()
    for r in records_with_retries:
        retried_outcomes[r.get("business_outcome", "unknown")] += 1

    # Retry success rate
    retry_success = sum(
        1 for r in records_with_retries
        if r.get("ai_processing_status") in AI_COMPLETED_STATUSES
    )

    return {
        "total_records": total,
        "records_with_retries": retry_count,
        "retry_rate": round(retry_count / total * 100, 2) if total > 0 else 0.0,
        "retry_success_rate": round(retry_success / retry_count * 100, 2) if retry_count > 0 else None,
        "retry_count_distribution": dict(retry_dist.most_common()),
        "retried_outcome_distribution": dict(retried_outcomes.most_common()),
    }


# ---------------------------------------------------------------------------
# Writeback analysis
# ---------------------------------------------------------------------------

async def get_writeback_analysis(
    ai_db,
    filters: AiAnalyticsFilters,
) -> Dict[str, Any]:
    """Writeback status analysis."""
    _validate_date_span(filters.start_date, filters.end_date)

    records, _, _ = await _load_normalized_cohort(ai_db, filters)
    records = _apply_filters(records, filters)

    total = len(records)
    status_counts: Counter = Counter()
    for r in records:
        status_counts[r.get("writeback_status", "unknown")] += 1

    # Failure breakdown by AI processing status
    failures = [r for r in records if r.get("writeback_status") == "failed_or_not_saved"]
    failure_by_cps: Counter = Counter()
    for r in failures:
        failure_by_cps[r.get("ai_processing_status", "unknown")] += 1

    return {
        "total_records": total,
        "status_distribution": dict(status_counts.most_common()),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / total * 100, 2) if total > 0 else 0.0,
        "failure_by_processing_status": dict(failure_by_cps.most_common()),
    }


# ---------------------------------------------------------------------------
# Agent stats
# ---------------------------------------------------------------------------

async def get_agent_stats(
    ai_db,
    filters: AiAnalyticsFilters,
) -> List[AiAgentStat]:
    """Agent execution statistics from ai_agent_conversations."""
    _validate_date_span(filters.start_date, filters.end_date)

    # For agent stats, we query the conversations collection directly
    # Build a date filter if provided
    match_stage: Dict[str, Any] = {}
    if filters.start_date or filters.end_date:
        date_filter: Dict[str, Any] = {}
        if filters.start_date:
            date_filter["$gte"] = filters.start_date
        if filters.end_date:
            try:
                end_exclusive = (
                    datetime.fromisoformat(filters.end_date) + timedelta(days=1)
                ).date().isoformat()
            except ValueError:
                end_exclusive = filters.end_date
            date_filter["$lt"] = end_exclusive
        match_stage["created_at"] = date_filter

    try:
        collection = ai_db[mongo_repo.AGENT_CONVERSATIONS_COLLECTION]
        pipeline = [
            {"$match": match_stage} if match_stage else {"$match": {}},
            {
                "$group": {
                    "_id": {
                        "agent": "$agent",
                        "status": "$status",
                        "processing_stage": "$processing_stage",
                        "request_type": "$request_type",
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
        ]
        cursor = collection.aggregate(pipeline)
        results = await cursor.to_list(length=1000)
    except Exception as e:
        logger.error(f"Failed to fetch agent stats: {e}")
        return []

    stats: List[AiAgentStat] = []
    for r in results:
        group = r.get("_id", {})
        stats.append(AiAgentStat(
            agent=group.get("agent", "unknown"),
            status=group.get("status", "unknown"),
            processing_stage=group.get("processing_stage", "unknown"),
            request_type=group.get("request_type", "unknown"),
            count=r.get("count", 0),
        ))

    return stats
