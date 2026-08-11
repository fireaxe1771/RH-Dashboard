import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from models import DashboardFilters
from target_db import target_db

logger = logging.getLogger(__name__)

AI_FEES_COLLECTION = "department_fees_resources"
AI_SEND_OPTIONS = {"auto", "queued", "limited_auto"}

# Rural Metro contract departments are excluded from this ranking.
EXCLUDED_DEPARTMENT_IDS = {1136, 2198, 2627, 2628, 2629}


class AiAdoptionResult(BaseModel):
    """Single department AI adoption + activity record."""
    rank_overall: int
    department_id: str
    department_name: Optional[str]
    state: Optional[str]
    drafts: int
    percent_of_total_volume: float
    ai_status: str
    ai_mode: str
    qualifying_fee_count: int = 0
    has_auto: bool = False
    has_queued: bool = False
    has_limited_auto: bool = False


class AiAdoptionSummary(BaseModel):
    active_departments: int
    departments_using_ai: int
    departments_not_using_ai: int
    departments_unknown: int
    total_drafts: int
    ai_department_drafts: int
    non_ai_department_drafts: int
    unknown_department_drafts: int
    ai_coverage_percent: float
    remaining_opportunity_percent: float


class AiAdoptionResponse(BaseModel):
    period: Dict[str, str]
    ai_status_basis: str
    summary: AiAdoptionSummary
    departments: List[AiAdoptionResult]


def _classify_fees(fees: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Determine AI status from a list of finalized fee/resource records."""
    qualifying = [
        f
        for f in fees
        if f.get("use_in_ai_process")
        and f.get("fee_send_option") in AI_SEND_OPTIONS
    ]
    options = {f.get("fee_send_option") for f in qualifying}

    if not qualifying:
        return {
            "uses_ai": False,
            "ai_mode": "not_using_ai",
            "qualifying_fee_count": 0,
            "has_auto": False,
            "has_queued": False,
            "has_limited_auto": False,
        }

    if len(options) > 1:
        mode = "mixed"
    elif "auto" in options:
        mode = "auto"
    elif "queued" in options:
        mode = "queued"
    else:
        mode = "limited_auto"

    return {
        "uses_ai": True,
        "ai_mode": mode,
        "qualifying_fee_count": len(qualifying),
        "has_auto": "auto" in options,
        "has_queued": "queued" in options,
        "has_limited_auto": "limited_auto" in options,
    }


async def get_ai_participation_map(
    ai_db,
    department_ids: Optional[List[int]] = None,
) -> Optional[Dict[int, Dict[str, Any]]]:
    """Fetch current AI participation for the requested department IDs.

    Returns ``None`` when the AI Mongo source cannot be reached so that
    failures are not misclassified as ``not_using_ai``.
    """
    query: Dict[str, Any] = {
        "fees_resources_final": {
            "$elemMatch": {
                "use_in_ai_process": True,
                "fee_send_option": {"$in": list(AI_SEND_OPTIONS)},
            }
        }
    }
    if department_ids is not None:
        query["department_id"] = {"$in": [int(d) for d in department_ids if d is not None]}

    projection = {
        "_id": 0,
        "department_id": 1,
        "department_name": 1,
        "fees_resources_final": 1,
    }

    try:
        rows = await ai_db[AI_FEES_COLLECTION].find(query, projection).to_list(
            length=None
        )
    except Exception as e:
        logger.error(f"Failed to read AI participation from MongoDB: {e}")
        return None

    result: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        raw_id = row.get("department_id")
        if raw_id is None:
            continue
        try:
            dept_id = int(raw_id)
        except (ValueError, TypeError):
            continue
        info = _classify_fees(row.get("fees_resources_final", []))
        info["department_name"] = row.get("department_name")
        result[dept_id] = info
    return result


def get_department_draft_activity(
    start_date: str,
    end_date: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Aggregate draft/submitted-run volume by department for the period."""
    sql = f"""
    SELECT TOP {limit}
        CAST(c.dept_id AS VARCHAR(50)) AS department_id,
        MAX(d.Name) AS department_name,
        MAX(d.physical_state) AS state,
        COUNT(DISTINCT c.id) AS drafts
    FROM (
        SELECT id, dept_id FROM Claims
        WHERE submitted = 0
          AND original_run_id IS NULL
          AND created BETWEEN %(start_date)s AND %(end_date)s
        UNION
        SELECT id, dept_id FROM Claims
        WHERE submitted = 1
          AND original_run_id IS NOT NULL
          AND created BETWEEN %(start_date)s AND %(end_date)s
    ) c
    LEFT JOIN Departments d ON d.ID = c.dept_id
    WHERE c.dept_id IS NOT NULL
      AND c.dept_id NOT IN (1136, 2198, 2627, 2628, 2629)
    GROUP BY CAST(c.dept_id AS VARCHAR(50))
    ORDER BY drafts DESC
    """

    filters = DashboardFilters(start_date=start_date, end_date=end_date)
    result = target_db.execute_read(sql, filters)
    return result.get("rows", [])


async def get_ai_adoption_report(
    ai_db,
    start_date: str,
    end_date: str,
    limit: int = 50,
    ai_status: str = "all",
) -> Dict[str, Any]:
    """Produce the AI Adoption dashboard payload for the selected period."""
    activity = get_department_draft_activity(start_date, end_date, limit)

    # Collect department IDs for a single targeted Mongo query.
    dept_ids: List[int] = []
    for row in activity:
        try:
            dept_id = int(row["department_id"])
        except (ValueError, TypeError):
            continue
        if dept_id not in EXCLUDED_DEPARTMENT_IDS:
            dept_ids.append(dept_id)

    participation = await get_ai_participation_map(ai_db, dept_ids)

    total_drafts = sum(int(row.get("drafts", 0) or 0) for row in activity)
    total_drafts = max(total_drafts, 1)  # avoid div/0; 0 still handled below

    departments: List[Dict[str, Any]] = []
    for rank, row in enumerate(activity, start=1):
        try:
            dept_id = int(row["department_id"])
        except (ValueError, TypeError):
            continue

        ai_info: Dict[str, Any]
        if participation is None:
            ai_info = {
                "uses_ai": False,
                "ai_mode": "unknown",
                "qualifying_fee_count": 0,
                "has_auto": False,
                "has_queued": False,
                "has_limited_auto": False,
            }
            status = "unknown"
        else:
            ai_info = participation.get(
                dept_id,
                {
                    "uses_ai": False,
                    "ai_mode": "not_using_ai",
                    "qualifying_fee_count": 0,
                    "has_auto": False,
                    "has_queued": False,
                    "has_limited_auto": False,
                },
            )
            status = "using_ai" if ai_info["uses_ai"] else "not_using_ai"

        if ai_status != "all" and status != ai_status:
            continue

        drafts = int(row.get("drafts", 0) or 0)
        pct = (drafts / total_drafts * 100) if total_drafts > 0 else 0.0
        departments.append(
            {
                "rank_overall": rank,
                "department_id": row.get("department_id"),
                "department_name": row.get("department_name"),
                "state": row.get("state"),
                "drafts": drafts,
                "percent_of_total_volume": round(pct, 2),
                "ai_status": status,
                "ai_mode": ai_info.get("ai_mode", status),
                "qualifying_fee_count": ai_info.get("qualifying_fee_count", 0),
                "has_auto": ai_info.get("has_auto", False),
                "has_queued": ai_info.get("has_queued", False),
                "has_limited_auto": ai_info.get("has_limited_auto", False),
            }
        )

    # Recalculate summary over the full activity set, not the filtered view.
    using_drafts = 0
    not_using_drafts = 0
    unknown_drafts = 0
    using = 0
    not_using = 0
    unknown = 0

    for row in activity:
        try:
            dept_id = int(row["department_id"])
        except (ValueError, TypeError):
            continue
        drafts = int(row.get("drafts", 0) or 0)

        if participation is None:
            unknown += 1
            unknown_drafts += drafts
        elif dept_id in participation:
            if participation[dept_id]["uses_ai"]:
                using += 1
                using_drafts += drafts
            else:
                not_using += 1
                not_using_drafts += drafts
        else:
            not_using += 1
            not_using_drafts += drafts

    active_departments = len(activity)
    total_drafts_for_pct = using_drafts + not_using_drafts + unknown_drafts

    ai_coverage = (
        (using_drafts / total_drafts_for_pct * 100) if total_drafts_for_pct else 0.0
    )
    remaining_opportunity = (
        (not_using_drafts / total_drafts_for_pct * 100)
        if total_drafts_for_pct
        else 0.0
    )

    summary = {
        "active_departments": active_departments,
        "departments_using_ai": using,
        "departments_not_using_ai": not_using,
        "departments_unknown": unknown,
        "total_drafts": total_drafts_for_pct,
        "ai_department_drafts": using_drafts,
        "non_ai_department_drafts": not_using_drafts,
        "unknown_department_drafts": unknown_drafts,
        "ai_coverage_percent": round(ai_coverage, 2),
        "remaining_opportunity_percent": round(remaining_opportunity, 2),
    }

    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "ai_status_basis": "current_configuration",
        "summary": summary,
        "departments": departments,
    }
