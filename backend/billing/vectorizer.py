"""AI vectorization pipeline: billing data -> NL documents -> embeddings -> Atlas Vector Search."""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from openai import AsyncAzureOpenAI, AsyncOpenAI

from config import settings
from billing import VectorizerError

logger = logging.getLogger(__name__)

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


@lru_cache(maxsize=1)
def _get_openai_client() -> AsyncOpenAI:
    """Returns a cached async client.

    Uses Azure OpenAI (Foundry) when AZURE_OPENAI_ENDPOINT is configured, otherwise
    falls back to the OpenAI.com API. AsyncAzureOpenAI is a subclass of AsyncOpenAI,
    so callers use the same chat/embeddings interface; for Azure the ``model``
    argument is the deployment name.
    """
    if settings.AZURE_OPENAI_ENDPOINT:
        if not settings.AZURE_OPENAI_API_KEY:
            raise VectorizerError("AZURE_OPENAI_API_KEY is not set.")
        return AsyncAzureOpenAI(
            api_key=settings.AZURE_OPENAI_API_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    if not settings.OPENAI_API_KEY:
        raise VectorizerError("OPENAI_API_KEY is not set.")
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Document generation
# --------------------------------------------------------------------------- #

async def _generate_cost_documents(db, billing_period: str) -> list[dict]:
    """top_spenders + service_cost_detail documents from azure_cost_summary."""
    docs: list[dict] = []
    service_rows = await db["azure_cost_summary"].find(
        {"period": billing_period, "dimension": "ServiceName"}
    ).to_list(length=None)
    if not service_rows:
        return docs

    service_rows.sort(key=lambda r: r.get("total_cost", 0.0), reverse=True)
    total_cost = sum(r.get("total_cost", 0.0) for r in service_rows)
    currency = service_rows[0].get("currency", "USD")
    year, month = (int(p) for p in billing_period.split("-"))
    month_name = _MONTH_NAMES[month]

    rg_rows = await db["azure_cost_summary"].find(
        {"period": billing_period, "dimension": "ResourceGroupName"}
    ).to_list(length=None)
    rg_rows.sort(key=lambda r: r.get("total_cost", 0.0), reverse=True)

    lines = [
        f"In {billing_period} ({month_name} {year}), Azure total spend was "
        f"${total_cost:,.2f} {currency} across 1 subscription(s).",
        "",
        "Top spending services:",
    ]
    for rank, row in enumerate(service_rows[:10], 1):
        cost = row.get("total_cost", 0.0)
        pct = (cost / total_cost * 100) if total_cost else 0.0
        lines.append(f"  {rank}. {row.get('dimension_value')}: ${cost:,.2f} ({pct:.1f}% of total)")
    lines.append("")
    lines.append("Resource groups with highest spend:")
    for row in rg_rows[:5]:
        lines.append(f"  - {row.get('dimension_value')}: ${row.get('total_cost', 0.0):,.2f}")

    docs.append({
        "document_type": "top_spenders",
        "text": "\n".join(lines),
        "metadata": {
            "period": billing_period,
            "dimension": "ServiceName",
            "dimension_value": "all_services",
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "source_collection": "azure_cost_summary",
        },
        "source_ids": [str(r.get("_id")) for r in service_rows],
    })

    for row in service_rows:
        cost = row.get("total_cost", 0.0)
        if cost <= 50.0:
            continue
        daily_avg = cost / 30.0
        change_pct = row.get("change_pct")
        text = (
            f"In {billing_period}, {row.get('dimension_value')} cost ${cost:,.2f} {currency} "
            f"(${daily_avg:,.2f}/day average).\n"
            f"Total usage: {row.get('usage_quantity', 0.0):,.1f} {row.get('unit_of_measure', '')}."
        )
        if change_pct is not None and change_pct > 10:
            text += f"\nALERT: Cost increased {change_pct:.1f}% vs prior month."
        docs.append({
            "document_type": "service_cost_detail",
            "text": text,
            "metadata": {
                "period": billing_period,
                "dimension": "ServiceName",
                "dimension_value": row.get("dimension_value"),
                "total_cost": round(cost, 2),
                "currency": currency,
                "source_collection": "azure_cost_summary",
            },
            "source_ids": [str(row.get("_id"))],
        })
    return docs


async def _generate_advisor_documents(db, billing_period: str) -> list[dict]:
    docs: list[dict] = []
    recs = await db["azure_advisor_recommendations"].find({"status": "Active"}).to_list(length=None)
    for rec in recs:
        impacted_field = rec.get("impacted_field", "")
        field_short = impacted_field.split("/")[-1] if impacted_field else ""
        text = (
            f"Azure Advisor {rec.get('impact')} Impact {rec.get('category')} Recommendation "
            f"(ID: {rec.get('recommendation_id')}):\n\n"
            f"Issue: {rec.get('problem_description')}\n"
            f"Recommended Action: {rec.get('solution_description')}\n"
            f"Affected Resource: {rec.get('impacted_value')} ({field_short}) "
            f"in resource group '{rec.get('resource_group')}' (subscription: {rec.get('subscription_id')})\n"
        )
        savings = rec.get("estimated_monthly_savings")
        if savings:
            annual = rec.get("estimated_annual_savings") or savings * 12
            text += (
                f"\nEstimated monthly savings: ${savings:,.2f} {rec.get('savings_currency')}\n"
                f"Estimated annual savings: ${annual:,.2f} {rec.get('savings_currency')}\n"
            )
        text += f"\nStatus: {rec.get('status')}."
        docs.append({
            "document_type": "advisor_recommendation",
            "text": text,
            "metadata": {
                "period": None,
                "dimension": "AdvisorRecommendation",
                "dimension_value": rec.get("impacted_value"),
                "estimated_monthly_savings": savings,
                "currency": rec.get("savings_currency"),
                "recommendation_id": rec.get("recommendation_id"),
                "source_collection": "azure_advisor_recommendations",
            },
            "source_ids": [str(rec.get("_id"))],
        })
    return docs


async def _generate_budget_documents(db, billing_period: str) -> list[dict]:
    docs: list[dict] = []
    budgets = await db["azure_budgets"].find({}).to_list(length=None)
    for b in budgets:
        util = b.get("utilization_pct", 0.0)
        amount = b.get("amount", 0.0)
        current = b.get("current_spend", 0.0)
        text = (
            f"Budget Status: '{b.get('budget_name')}' ({b.get('time_grain')} budget, scope: {b.get('scope')})\n\n"
            f"Budget limit: ${amount:,.2f} {b.get('current_spend_currency', 'USD')}\n"
            f"Current spend: ${current:,.2f} ({util:.1f}% utilized)\n"
        )
        if util >= 100:
            text += f"\nCRITICAL: Budget has been EXCEEDED by ${current - amount:,.2f}."
        elif util >= 80:
            text += f"\nWARNING: Budget is {util:.1f}% utilized — approaching limit."
        else:
            text += "\nBudget is within normal utilization range."
        text += f"\n\nBudget period: {b.get('time_period_start')} to {b.get('time_period_end')}."
        docs.append({
            "document_type": "budget_status",
            "text": text,
            "metadata": {
                "period": billing_period,
                "dimension": "Budget",
                "dimension_value": b.get("budget_name"),
                "total_cost": current,
                "currency": b.get("current_spend_currency", "USD"),
                "source_collection": "azure_budgets",
            },
            "source_ids": [str(b.get("_id"))],
        })
    return docs


async def _generate_reservation_documents(db, billing_period: str) -> list[dict]:
    docs: list[dict] = []
    recs = await db["azure_reservation_recommendations"].find({"net_savings": {"$gt": 0}}).to_list(length=None)
    for r in recs:
        term = r.get("term", "")
        term_friendly = "1-year" if term == "P1Y" else "3-year" if term == "P3Y" else term
        net = r.get("net_savings", 0.0)
        paygo = r.get("total_cost_with_no_ri", 0.0)
        ri = r.get("total_cost_with_ri", 0.0)
        savings_pct = (net / paygo * 100) if paygo else 0.0
        currency = r.get("currency", "USD")
        text = (
            "Reserved Instance Purchase Opportunity:\n\n"
            f"Resource type: {r.get('resource_type')} ({r.get('sku_name')}) in {r.get('location')}\n"
            f"Look-back analysis: Based on {r.get('look_back_period')} of usage data.\n"
            f"Term: {term_friendly} reserved instance\n\n"
            f"Current pay-as-you-go monthly cost: ${paygo:,.2f} {currency}\n"
            f"Cost with {r.get('recommended_quantity')} reserved instance(s): ${ri:,.2f} {currency}\n"
            f"Monthly savings: ${net:,.2f} {currency} ({savings_pct:.1f}% reduction)\n"
            f"Annual savings: ${net * 12:,.2f} {currency}\n"
        )
        docs.append({
            "document_type": "reservation_opportunity",
            "text": text,
            "metadata": {
                "period": billing_period,
                "dimension": "ReservationOpportunity",
                "dimension_value": f"{r.get('sku_name')}_{r.get('location')}_{term}",
                "estimated_monthly_savings": net,
                "currency": currency,
                "source_collection": "azure_reservation_recommendations",
            },
            "source_ids": [str(r.get("_id"))],
        })
    return docs


async def _generate_invoice_documents(db, billing_period: str) -> list[dict]:
    docs: list[dict] = []
    invoices = await db["azure_invoices"].find(
        {"billing_period_start": {"$regex": f"^{billing_period}"}}
    ).to_list(length=None)
    for inv in invoices:
        text = (
            "Azure Invoice Summary:\n\n"
            f"Invoice ID: {inv.get('invoice_id')}\n"
            f"Billing period: {inv.get('billing_period_start')} to {inv.get('billing_period_end')}\n"
            f"Invoice date: {inv.get('invoice_date')}\n\n"
            f"Total charges: ${inv.get('billed_amount', 0.0):,.2f} {inv.get('billing_currency', 'USD')}\n"
            f"Amount due: ${inv.get('amount_due', 0.0):,.2f} {inv.get('billing_currency', 'USD')}\n\n"
            f"Payment status: {inv.get('status')}"
        )
        if inv.get("status") == "PastDue":
            text += "\nALERT: This invoice is PAST DUE."
        docs.append({
            "document_type": "invoice_summary",
            "text": text,
            "metadata": {
                "period": (inv.get("billing_period_start") or "")[:7],
                "dimension": "Invoice",
                "dimension_value": inv.get("invoice_id"),
                "total_cost": inv.get("billed_amount", 0.0),
                "currency": inv.get("billing_currency", "USD"),
                "source_collection": "azure_invoices",
            },
            "source_ids": [str(inv.get("_id"))],
        })
    return docs


async def generate_billing_documents(db, billing_period: str) -> list[dict]:
    """Generates all document types for the given billing period."""
    docs: list[dict] = []
    docs.extend(await _generate_cost_documents(db, billing_period))
    docs.extend(await _generate_advisor_documents(db, billing_period))
    docs.extend(await _generate_budget_documents(db, billing_period))
    docs.extend(await _generate_reservation_documents(db, billing_period))
    docs.extend(await _generate_invoice_documents(db, billing_period))
    return docs


# --------------------------------------------------------------------------- #
# Embedding pipeline
# --------------------------------------------------------------------------- #

async def embed_documents(documents: list[dict]) -> list[dict]:
    """Generates embeddings for documents in batches of 100, sleeping between batches."""
    client = _get_openai_client()
    BATCH_SIZE = 100

    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i:i + BATCH_SIZE]
        texts = [doc["text"] for doc in batch]
        try:
            response = await client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=texts,
            )
            for j, embedding_data in enumerate(response.data):
                batch[j]["embedding"] = embedding_data.embedding
        except Exception as e:  # noqa: BLE001
            raise VectorizerError(f"Embedding generation failed for batch {i // BATCH_SIZE}: {e}")

        if i + BATCH_SIZE < len(documents):
            await asyncio.sleep(0.5)

    return documents


async def upsert_vectors(db, documents: list[dict]) -> int:
    """Upserts vectorized documents into azure_billing_vectors collection."""
    upserted = 0
    now = _now()
    for doc in documents:
        filter_key = {
            "document_type": doc["document_type"],
            "metadata.period": doc["metadata"].get("period"),
            "metadata.dimension_value": doc["metadata"].get("dimension_value"),
        }
        update = {
            "$set": {
                **doc,
                "model": settings.OPENAI_EMBEDDING_MODEL,
                "dimensions": len(doc.get("embedding", [])),
                "last_updated": now,
            },
            "$setOnInsert": {"created_at": now},
        }
        result = await db["azure_billing_vectors"].update_one(filter_key, update, upsert=True)
        if result.upserted_id or result.modified_count:
            upserted += 1
    return upserted


async def run_vectorization(db) -> int:
    """Generates and upserts embeddings for the current and prior billing periods."""
    today = date.today()
    current_period = today.strftime("%Y-%m")
    prior_month = today.replace(day=1) - timedelta(days=1)
    prior_period = prior_month.strftime("%Y-%m")

    total = 0
    for period in [prior_period, current_period]:
        docs = await generate_billing_documents(db, period)
        if not docs:
            continue
        docs_with_embeddings = await embed_documents(docs)
        count = await upsert_vectors(db, docs_with_embeddings)
        total += count
        logger.info(f"Vectorized {count} documents for period {period}")
    return total


# --------------------------------------------------------------------------- #
# Semantic search
# --------------------------------------------------------------------------- #

async def semantic_search(
    db,
    query_text: str,
    document_types: list[str] | None = None,
    period_filter: str | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Embeds query_text and returns the top_k most similar billing documents."""
    client = _get_openai_client()
    response = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=[query_text],
    )
    query_embedding = response.data[0].embedding

    vector_search_stage = {
        "$vectorSearch": {
            "index": "billing_vector_index",
            "path": "embedding",
            "queryVector": query_embedding,
            "numCandidates": top_k * 10,
            "limit": top_k,
        }
    }

    if document_types or period_filter:
        filter_conditions: dict = {}
        if document_types:
            filter_conditions["document_type"] = {"$in": document_types}
        if period_filter:
            filter_conditions["metadata.period"] = period_filter
        vector_search_stage["$vectorSearch"]["filter"] = filter_conditions

    pipeline = [
        vector_search_stage,
        {
            "$project": {
                "embedding": 0,
                "score": {"$meta": "vectorSearchScore"},
                "document_type": 1,
                "text": 1,
                "metadata": 1,
            }
        },
    ]

    results = await db["azure_billing_vectors"].aggregate(pipeline).to_list(length=top_k)
    return results
