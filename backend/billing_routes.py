import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from auth import get_current_user
from config import settings
from database import get_db
from billing import sync_service, vectorizer
from billing_models import (
    BillingSyncRequest,
    BillingAIQueryRequest,
    BillingAIQueryResponse,
    AIQuerySource,
    AdvisorSummaryResponse,
    SyncStatusEntry,
    SyncStatusResponse,
)

logger = logging.getLogger(__name__)

# Router mounted under the "/api/billing" prefix in main.py
billing_router = APIRouter(tags=["Azure Billing"])

# Allowed manual sync types -> sync_service coroutine
_SYNC_DISPATCH = {
    "full": lambda db: sync_service.run_full_backfill(db, settings.BILLING_HISTORY_MONTHS, "manual_api"),
    "daily": lambda db: sync_service.run_daily_sync(db),
    "advisor": lambda db: sync_service.sync_advisor_recommendations(db, "manual_api"),
    "budgets": lambda db: sync_service.sync_budgets(db, "manual_api"),
    "alerts": lambda db: sync_service.sync_alerts(db, "manual_api"),
    "invoices": lambda db: sync_service.sync_invoices(db, "manual_api"),
    "reservations": lambda db: sync_service.sync_reservations(db, "manual_api"),
    "resource_inventory": lambda db: sync_service.sync_resource_inventory(db, "manual_api"),
    "retail_prices": lambda db: sync_service.sync_retail_prices(db, "manual_api"),
    "vectorize": lambda db: vectorizer.run_vectorization(db),
}


def _serialize(doc: dict) -> dict:
    """Converts a MongoDB document's ObjectId to a string id field."""
    if not doc:
        return {}
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
        doc["id"] = doc["_id"]
    return doc


def _current_period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


# --------------------------------------------------------------------------- #
# Sync management
# --------------------------------------------------------------------------- #

@billing_router.get(
    "/sync/status",
    response_model=SyncStatusResponse,
    dependencies=[Depends(get_current_user)],
)
async def get_sync_status(db=Depends(get_db)):
    """Returns the latest sync log entry per sync_type."""
    logs = await db["azure_billing_sync_log"].find({}).sort("started_at", -1).to_list(length=500)
    latest: dict[str, dict] = {}
    for log in logs:
        sync_type = log.get("sync_type")
        if sync_type not in latest:
            latest[sync_type] = log

    entries = [
        SyncStatusEntry(
            sync_type=log.get("sync_type", ""),
            status=log.get("status", ""),
            last_run=log.get("started_at"),
            last_period=log.get("billing_period"),
            records_synced=log.get("records_synced", 0),
            duration_seconds=log.get("duration_seconds"),
            error_message=log.get("error_message"),
        )
        for log in latest.values()
    ]
    return SyncStatusResponse(syncs=entries)


@billing_router.post("/sync/trigger", dependencies=[Depends(get_current_user)])
async def trigger_sync(
    request: BillingSyncRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    """Validates the sync type and dispatches the sync as a background task."""
    if request.sync_type not in _SYNC_DISPATCH:
        raise HTTPException(status_code=400, detail=f"Invalid sync_type: {request.sync_type}")

    dispatch = _SYNC_DISPATCH[request.sync_type]
    if request.sync_type == "cost_details" and request.billing_period:
        background_tasks.add_task(sync_service.sync_cost_details, db, request.billing_period, "manual_api")
    else:
        background_tasks.add_task(dispatch, db)
    return {"status": "queued", "sync_type": request.sync_type}


# --------------------------------------------------------------------------- #
# Cost analytics
# --------------------------------------------------------------------------- #

@billing_router.get("/cost/summary", dependencies=[Depends(get_current_user)])
async def cost_summary(
    db=Depends(get_db),
    period: str = Query(default_factory=_current_period),
    dimension: str = "ServiceName",
):
    rows = await db["azure_cost_summary"].find(
        {"period": period, "dimension": dimension}
    ).sort("total_cost", -1).to_list(length=None)
    items = [_serialize(r) for r in rows]
    total = sum(r.get("total_cost", 0.0) for r in rows)
    currency = rows[0].get("currency", "USD") if rows else "USD"
    return {"items": items, "total": round(total, 2), "currency": currency, "period": period}


@billing_router.get("/cost/trend", dependencies=[Depends(get_current_user)])
async def cost_trend(
    db=Depends(get_db),
    months: int = 12,
    dimension: str = "ServiceName",
    dimension_value: str | None = None,
):
    query: dict = {"dimension": dimension}
    if dimension_value:
        query["dimension_value"] = dimension_value
    rows = await db["azure_cost_summary"].find(query).sort("period", 1).to_list(length=None)
    return [_serialize(r) for r in rows][-months * 50:]


@billing_router.get("/cost/top-spenders", dependencies=[Depends(get_current_user)])
async def cost_top_spenders(
    db=Depends(get_db),
    period: str = Query(default_factory=_current_period),
    dimension: str = "ServiceName",
    limit: int = 10,
):
    rows = await db["azure_cost_summary"].find(
        {"period": period, "dimension": dimension}
    ).sort("total_cost", -1).to_list(length=limit)
    return [_serialize(r) for r in rows]


@billing_router.get("/cost/by-tag", dependencies=[Depends(get_current_user)])
async def cost_by_tag(
    db=Depends(get_db),
    period: str = Query(default_factory=_current_period),
    tag_key: str = "",
    limit: int = 20,
):
    rows = await db["azure_cost_details"].find({"billing_period": period}).to_list(length=None)
    buckets: dict[str, float] = {}
    for row in rows:
        value = (row.get("tags") or {}).get(tag_key, "untagged")
        buckets[value] = buckets.get(value, 0.0) + row.get("pre_tax_cost", 0.0)
    ranked = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"tag_value": k, "total_cost": round(v, 2)} for k, v in ranked]


@billing_router.get("/cost/daily", dependencies=[Depends(get_current_user)])
async def cost_daily(
    db=Depends(get_db),
    start_date: str = "",
    end_date: str = "",
    service_name: str | None = None,
):
    query: dict = {}
    if start_date and end_date:
        query["date"] = {"$gte": start_date, "$lte": end_date}
    if service_name:
        query["service_name"] = service_name
    rows = await db["azure_cost_details"].find(query).to_list(length=None)
    buckets: dict[str, float] = {}
    for row in rows:
        day = row.get("date", "")
        buckets[day] = buckets.get(day, 0.0) + row.get("pre_tax_cost", 0.0)
    return [{"date": d, "total_cost": round(c, 2)} for d, c in sorted(buckets.items())]


@billing_router.get("/cost/forecast", dependencies=[Depends(get_current_user)])
async def cost_forecast(db=Depends(get_db)):
    rows = await db["azure_cost_summary"].find({"dimension": "Forecast"}).to_list(length=None)
    return [_serialize(r) for r in rows]


# --------------------------------------------------------------------------- #
# Budgets & alerts
# --------------------------------------------------------------------------- #

@billing_router.get("/budgets", dependencies=[Depends(get_current_user)])
async def list_budgets(db=Depends(get_db)):
    rows = await db["azure_budgets"].find({}).sort("utilization_pct", -1).to_list(length=None)
    return [_serialize(r) for r in rows]


@billing_router.get("/alerts", dependencies=[Depends(get_current_user)])
async def list_alerts(db=Depends(get_db)):
    rows = await db["azure_cost_alerts"].find({"status": "Active"}).sort("creation_time", -1).to_list(length=None)
    return [_serialize(r) for r in rows]


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #

@billing_router.get("/advisor/recommendations", dependencies=[Depends(get_current_user)])
async def advisor_recommendations(
    db=Depends(get_db),
    category: str | None = None,
    impact: str | None = None,
    status: str = "Active",
):
    query: dict = {"status": status}
    if category:
        query["category"] = category
    if impact:
        query["impact"] = impact
    rows = await db["azure_advisor_recommendations"].find(query).sort("estimated_monthly_savings", -1).to_list(length=None)
    return [_serialize(r) for r in rows]


@billing_router.get("/advisor/cost-savings", dependencies=[Depends(get_current_user)])
async def advisor_cost_savings(db=Depends(get_db)):
    rows = await db["azure_advisor_recommendations"].find(
        {"category": "Cost", "status": "Active"}
    ).sort("estimated_monthly_savings", -1).to_list(length=None)
    return [_serialize(r) for r in rows]


@billing_router.get(
    "/advisor/summary",
    response_model=AdvisorSummaryResponse,
    dependencies=[Depends(get_current_user)],
)
async def advisor_summary(db=Depends(get_db)):
    rows = await db["azure_advisor_recommendations"].find({"status": "Active"}).to_list(length=None)
    by_impact: dict[str, int] = {}
    total_savings = 0.0
    cost_count = 0
    currency = "USD"
    for r in rows:
        impact = r.get("impact", "Unknown")
        by_impact[impact] = by_impact.get(impact, 0) + 1
        if r.get("category") == "Cost":
            cost_count += 1
            total_savings += r.get("estimated_monthly_savings") or 0.0
            currency = r.get("savings_currency") or currency
    return AdvisorSummaryResponse(
        total_recommendations=len(rows),
        cost_recommendations=cost_count,
        total_monthly_savings=round(total_savings, 2),
        currency=currency,
        by_impact=by_impact,
    )


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #

@billing_router.get("/invoices", dependencies=[Depends(get_current_user)])
async def list_invoices(db=Depends(get_db)):
    rows = await db["azure_invoices"].find({}).sort("billing_period_start", -1).to_list(length=None)
    return [_serialize(r) for r in rows]


@billing_router.get("/invoices/{invoice_id}", dependencies=[Depends(get_current_user)])
async def get_invoice(invoice_id: str, db=Depends(get_db)):
    invoice = await db["azure_invoices"].find_one({"invoice_id": invoice_id})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return _serialize(invoice)


# --------------------------------------------------------------------------- #
# Reservations
# --------------------------------------------------------------------------- #

@billing_router.get("/reservations/details", dependencies=[Depends(get_current_user)])
async def reservation_details(
    db=Depends(get_db),
    billing_period: str = Query(default_factory=_current_period),
):
    rows = await db["azure_reservation_details"].find({"billing_period": billing_period}).to_list(length=None)
    return [_serialize(r) for r in rows]


@billing_router.get("/reservations/recommendations", dependencies=[Depends(get_current_user)])
async def reservation_recommendations(db=Depends(get_db)):
    rows = await db["azure_reservation_recommendations"].find({}).sort("net_savings", -1).to_list(length=None)
    return [_serialize(r) for r in rows]


# --------------------------------------------------------------------------- #
# AI query
# --------------------------------------------------------------------------- #

BILLING_AI_SYSTEM_PROMPT = """You are an expert Azure cloud cost optimization analyst for RecoveryHub. 
You have deep expertise in Azure pricing models, cost management best practices, and FinOps principles.

You have been provided with relevant billing data retrieved from the Azure Cost Management, Azure Advisor, 
and related billing APIs. The data has been pre-filtered to be most relevant to the user's question.

When analyzing costs and providing recommendations:
1. ALWAYS quantify savings opportunities in dollar amounts when the data provides them.
2. Prioritize recommendations by potential monthly savings (highest impact first).
3. Distinguish clearly between:
   - "Quick wins" — immediate actions with no service disruption (e.g., resize/stop idle VMs)
   - "Planned changes" — require change management or testing (e.g., reserved instance purchases)
   - "Architectural improvements" — longer-term optimization (e.g., service tier changes)
4. Flag cost anomalies (day-over-day spikes) with specific investigation steps.
5. When budget thresholds are exceeded or forecast to be exceeded, call this out prominently.
6. Cross-reference Azure Advisor recommendations with actual cost data to validate impact.
7. For reservation recommendations, show the payback period and total annual savings.
8. Always specify: (a) what to change, (b) which specific resource/service, (c) estimated monthly savings.

Be specific. Avoid generic advice. Use the actual resource names, resource groups, and dollar 
amounts from the provided data.

If the data does not contain enough information to answer the question fully, state what 
additional data would be needed (e.g., "this would require 90-day trend data which is not in 
the current dataset").

Billing context data:
{context}"""


@billing_router.post(
    "/ai/query",
    response_model=BillingAIQueryResponse,
    dependencies=[Depends(get_current_user)],
)
async def billing_ai_query(request: BillingAIQueryRequest, db=Depends(get_db)):
    """Processes a natural language cost analysis query using semantic search + LLM synthesis."""
    from billing import VectorizerError

    try:
        relevant_docs = await vectorizer.semantic_search(
            db=db,
            query_text=request.question,
            document_types=request.document_types,
            period_filter=request.period_filter,
            top_k=request.top_k,
        )

        if not relevant_docs:
            return BillingAIQueryResponse(
                answer="No billing data is available for analysis yet. Please trigger a billing sync first via POST /api/billing/sync/trigger.",
                sources=[],
                model=settings.OPENAI_CHAT_MODEL,
                question=request.question,
            )

        context_parts = []
        for i, doc in enumerate(relevant_docs, 1):
            context_parts.append(
                f"[{i}] {doc['document_type'].upper()} (relevance: {doc.get('score', 0):.3f}):\n{doc['text']}"
            )
        context_text = "\n\n---\n\n".join(context_parts)

        client = vectorizer._get_openai_client()
        chat_response = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": BILLING_AI_SYSTEM_PROMPT.format(context=context_text)},
                {"role": "user", "content": request.question},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        answer = chat_response.choices[0].message.content

        sources = [
            AIQuerySource(
                document_type=doc["document_type"],
                period=doc["metadata"].get("period"),
                dimension_value=doc["metadata"].get("dimension_value"),
                total_cost=doc["metadata"].get("total_cost"),
                score=doc.get("score", 0.0),
            )
            for doc in relevant_docs
        ]
        return BillingAIQueryResponse(
            answer=answer,
            sources=sources,
            model=settings.OPENAI_CHAT_MODEL,
            question=request.question,
        )
    except VectorizerError as e:
        logger.error(f"AI query vectorizer error: {e}")
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {e}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"AI query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to process AI query.")
