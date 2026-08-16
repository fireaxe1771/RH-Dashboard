"""Measure v2 projection BSON sizes for representative claim shapes.

Phase 10 added nested ``resources`` to line items and ``conversation_summaries``
to the projection. The v1 sizing estimate (~2-3 KB/doc) no longer applies.
This script builds small / median / large representative projections using
``build_projection`` and reports their BSON-encoded size so the Section 9.13
retention policy can be re-validated.

Run from backend/:
    $env:TESTING="true"; .venv\\Scripts\\python.exe scripts\\measure_v2_projection_size.py

No external I/O — purely synthetic data shaped to match the verified production
schema (Phase 0 Sections 4.2 and 4.6). Numbers are approximate; production
should be re-measured against a real sample after backfill.

Source: synthetic ai_line_items + ai_agent_conversations dicts.
Destination: stdout only (no writes).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure backend/ is on sys.path so the worker package imports resolve
# when run as a script.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("TESTING", "true")

from bson import ObjectId  # noqa: E402

from ai_analytics_worker.projection_builder import build_projection  # noqa: E402

try:
    from bson import encode as bson_encode  # pymongo >=4.7
except ImportError:  # pragma: no cover - older pymongo fallback
    from bson import BSON
    def bson_encode(doc):
        return BSON.encode(doc)


PROCESSED_AT = datetime(2026, 8, 13, 12, 0, 0)


def _resource(label: str, qty: float, amount: float, unit: float = 1.0) -> dict:
    return {
        "resourceLabel": label,
        "quantity": qty,
        "amount": amount,
        "unit": unit,
    }


def _line_item(
    item: str,
    description: str,
    qty: float,
    rate: float,
    total: float,
    resources: list[dict] | None = None,
) -> dict:
    return {
        "item": item,
        "description": description,
        "quantity": qty,
        "rate": rate,
        "line_item_total": total,
        "resources": resources or [],
    }


def _conversation(
    agent: str,
    created_at: datetime,
    status: str = "completed",
    stage: str = "completed_all_agents",
    request_type: str = "incident_analysis",
    exec_seconds: float | None = None,
) -> dict:
    return {
        "_id": ObjectId(),
        "agent": agent,
        "status": status,
        "created_at": created_at,
        "processing_stage": stage,
        "request_type": request_type,
        "execution_time_seconds": exec_seconds,
        # Large payload fields are NOT in conversation_summaries, but the
        # builder ignores them when summarizing — keep them out of the
        # synthetic source so the source-doc size doesn't muddy the
        # projection measurement.
    }


def _ai_line_items(
    claim_id: int,
    line_items: list[dict],
    conversations_count: int = 0,
    billing_category: str | None = "Motor Vehicle Accident",
    review_msg: str | None = "Auto-approved",
) -> dict:
    base = datetime(2026, 7, 1, 9, 0, 0)
    return {
        "_id": ObjectId(),
        "claim_id": claim_id,
        "draft_claim_id": claim_id + 100000,
        "run_number": f"RUN-{claim_id}",
        "department_id": 42,
        "department_name": "Springfield Fire Department",
        "inserted_at": base,
        "updated_at": base + timedelta(hours=1, minutes=30),
        "completed_at": base + timedelta(hours=1, minutes=25),
        "processing_time_seconds": 12.5,
        "claim_processing_status": "COMPLETED",
        "agent_exec_status": "success",
        "confidence_level": 85,
        "review_msg": review_msg,
        "is_billable": None,
        "is_billable_not_determined": None,
        "billing_category": billing_category,
        "incident_duration_in_minutes": 45,
        "line_items_save_to_rh_status": True,
        "retry_count": 0,
        "thread_id": None,
        "retry_thread_id": None,
        "thread_id_is_billable": None,
        "conversation_id": str(ObjectId()),
        "invoice_total": sum(li["line_item_total"] for li in line_items),
        "line_items": line_items,
    }


def _conversations(n: int, base: datetime) -> list[dict]:
    convs: list[dict] = []
    for i in range(n):
        convs.append(
            _conversation(
                agent="multi_agent_workflow",
                created_at=base + timedelta(minutes=i * 3),
                exec_seconds=8.4 + i if i % 3 == 0 else None,
            )
        )
    return convs


def _report(label: str, projection: dict) -> dict:
    size = len(bson_encode(projection))
    # Sum the variable-cost components so the report can attribute growth.
    # bson.encode requires a top-level mapping, so wrap lists in a dict and
    # subtract the wrapper overhead (~5 bytes for an empty BSON document).
    line_items_bytes = max(
        0, len(bson_encode({"v": projection.get("ai_line_items", [])})) - 5
    )
    summaries_bytes = max(
        0,
        len(bson_encode({"v": projection.get("conversation_summaries", [])})) - 5,
    )
    print(
        f"{label:28s} total={size:6d}B  "
        f"line_items={line_items_bytes:5d}B  "
        f"summaries={summaries_bytes:5d}B  "
        f"schema_v{projection.get('projection_schema_version')}"
    )
    return {
        "label": label,
        "total_bytes": size,
        "line_items_bytes": line_items_bytes,
        "summaries_bytes": summaries_bytes,
    }


def main() -> int:
    print("=" * 78)
    print("v2 projection BSON sizing — synthetic representative shapes")
    print("=" * 78)

    # --- Small: 1 line item, no resources, 1 conversation ----------------
    small_ai = _ai_line_items(
        claim_id=1001,
        line_items=[
            _line_item("Basic response", "1 unit", 1, 250.0, 250.0),
        ],
    )
    small_proj = build_projection(
        1001, small_ai, _conversations(1, datetime(2026, 7, 1, 9, 5)), PROCESSED_AT
    )

    # --- Median: 4 line items with 2-3 resources each, 2 conversations ---
    median_ai = _ai_line_items(
        claim_id=2002,
        line_items=[
            _line_item(
                "Engine usage", "Engine 1 - 2 hours", 2, 500.0, 1000.0,
                resources=[
                    _resource("Fuel", 10, 50.0),
                    _resource("Oil", 1, 25.0),
                ],
            ),
            _line_item(
                "Personnel", "3 firefighters - 1 hour", 3, 166.67, 500.01,
                resources=[
                    _resource("Officer", 1, 200.0),
                    _resource("Firefighter", 2, 150.0),
                    _resource("Driver", 1, 100.0),
                ],
            ),
            _line_item(
                "Equipment", "Ladder deployment", 1, 300.0, 300.0,
                resources=[_resource("Ladder truck", 1, 300.0)],
            ),
            _line_item("Consumables", "Foam 5 gal", 5, 40.0, 200.0),
        ],
    )
    median_proj = build_projection(
        2002, median_ai, _conversations(2, datetime(2026, 7, 1, 9, 5)), PROCESSED_AT
    )

    # --- Large: 12 line items, 3-5 resources each, 5 conversations -------
    large_items: list[dict] = []
    for i in range(12):
        n_res = 3 + (i % 3)  # 3, 4, 5, 3, 4, 5, ...
        resources = [
            _resource(f"Resource {i}-{r}", float(r + 1), float((r + 1) * 25.0))
            for r in range(n_res)
        ]
        large_items.append(
            _line_item(
                item=f"Line item {i}",
                description=f"Description for item {i} with some extra prose "
                            f"to simulate real review text length",
                qty=float(i + 1),
                rate=125.0 + i * 10,
                total=(125.0 + i * 10) * (i + 1),
                resources=resources,
            )
        )
    large_ai = _ai_line_items(
        claim_id=3003,
        line_items=large_items,
        review_msg=(
            "Auto-approved with manual review of nested resource fees; "
            "the AI flagged consumables alignment and recommended re-pricing "
            "the second-tier resource mapping for the second engine."
        ),
    )
    large_proj = build_projection(
        3003, large_ai, _conversations(5, datetime(2026, 7, 1, 9, 5)), PROCESSED_AT
    )

    # --- Edge: 0 line items, 0 conversations (tombstone-like) -----------
    empty_ai = _ai_line_items(claim_id=4004, line_items=[], billing_category=None)
    empty_proj = build_projection(
        4004, empty_ai, [], PROCESSED_AT
    )

    results = [
        _report("small (1 li, 0 res, 1 conv)", small_proj),
        _report("median (4 li, ~2 res, 2 conv)", median_proj),
        _report("large (12 li, 3-5 res, 5 conv)", large_proj),
        _report("empty (0 li, 0 conv)", empty_proj),
    ]

    # --- Aggregate annual-growth re-estimate -----------------------------
    # Per Phase 0: ~2,000 new docs/month. Use the median as the
    # representative doc size (most claims have a handful of line items
    # and 1-2 conversations per Phase 0 audit).
    median_bytes = results[1]["total_bytes"]
    monthly_docs = 2000
    annual_docs = monthly_docs * 12
    annual_mb = (median_bytes * annual_docs) / (1024 * 1024)
    ten_year_mb = annual_mb * 10

    print()
    print(f"Representative median doc:    {median_bytes} bytes")
    print(f"Annual growth (~{annual_docs} docs):  {annual_mb:.1f} MB")
    print(f"10-year projection:           {ten_year_mb:.1f} MB")
    print(f"MongoDB 16 MB doc limit:      {'OK' if max(r['total_bytes'] for r in results) < 16_000_000 else 'WARNING'}")
    print()
    print("Note: synthetic data — re-measure against a production sample")
    print("after the v2 backfill completes before treating these as final.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
