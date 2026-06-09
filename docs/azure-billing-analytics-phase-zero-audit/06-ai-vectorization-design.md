# Supporting Doc 06 — AI Vectorization Design

**Project:** RecoveryHub Dashboard System  
**Purpose:** Complete specification for the billing data vectorization pipeline, semantic search, and AI query endpoint including document types, text generation templates, embedding strategy, and LLM prompts.

---

## Overview

The vectorization layer transforms structured MongoDB billing data into semantically rich natural language documents, generates vector embeddings using OpenAI's `text-embedding-3-small` model, and stores them in the `azure_billing_vectors` collection for MongoDB Atlas Vector Search.

When a user submits a natural language question via the AI Cost Analyst, the system:
1. Embeds the question using the same model
2. Performs a vector similarity search against stored billing documents
3. Retrieves the most semantically relevant billing context
4. Passes that context to an LLM (GPT-4o-mini) for synthesis
5. Returns a structured, sourced answer

---

## 1. Module: `backend/billing/vectorizer.py`

### Dependencies
```python
import logging
import asyncio
from datetime import datetime, timezone
from openai import AsyncOpenAI
from motor.motor_asyncio import AsyncIOMotorDatabase
from config import settings
from billing import VectorizerError

logger = logging.getLogger(__name__)
```

### OpenAI Client
```python
from functools import lru_cache

@lru_cache(maxsize=1)
def _get_openai_client() -> AsyncOpenAI:
    """Returns a cached AsyncOpenAI client instance."""
    if not settings.OPENAI_API_KEY:
        raise VectorizerError("OPENAI_API_KEY is not set.")
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
```

---

## 2. Document Types

Seven document types are generated from MongoDB billing data. Each is a natural language text designed to be semantically useful for cost analysis queries.

### 2.1 `top_spenders` — Monthly Cost Breakdown by Service

**Source collection:** `azure_cost_summary`  
**Generated:** One document per billing period, summarizing top services

**Text template:**
```
In {period} ({month_name} {year}), Azure total spend was ${total_cost:,.2f} {currency} across 
{subscription_count} subscription(s). 

Top spending services:
{for each service in top_10_services}
  {rank}. {service_name}: ${cost:,.2f} ({pct:.1f}% of total){' [+{change:.1f}%]' if change > 5 else ' [-{change:.1f}%]' if change < -5 else ''} vs prior month
{end}

Resource groups with highest spend:
{for each rg in top_5_resource_groups}
  - {resource_group}: ${cost:,.2f}
{end}

Total monthly change vs prior period: {'+' if change_pct > 0 else ''}{change_pct:.1f}%.
```

**Metadata:**
```python
{
    "period": "2026-05",
    "dimension": "ServiceName",
    "dimension_value": "all_services",
    "total_cost": 42831.12,
    "currency": "USD",
    "source_collection": "azure_cost_summary"
}
```

---

### 2.2 `service_cost_detail` — Per-Service Monthly Summary

**Source collection:** `azure_cost_summary`  
**Generated:** One document per service per billing period

**Text template:**
```
In {period}, {service_name} cost ${total_cost:,.2f} {currency} (${daily_avg:,.2f}/day average).
This service is categorized under the {service_family} family.
Top resource groups: {top_3_rg_list}.
{If change_pct > 10: 'ALERT: Cost increased {change_pct:.1f}% vs prior month (${change_amount:,.2f} more).'}
{If change_pct < -10: 'Cost decreased {abs(change_pct):.1f}% vs prior month (${abs(change_amount):,.2f} less).'}
Total usage: {quantity:,.1f} {unit_of_measure}.
```

**Metadata:**
```python
{
    "period": "2026-05",
    "dimension": "ServiceName",
    "dimension_value": service_name,
    "total_cost": 4821.44,
    "currency": "USD",
    "source_collection": "azure_cost_summary"
}
```

Only generate this document type for services where `total_cost > 50.0` to avoid noise from zero-cost or negligible services.

---

### 2.3 `advisor_recommendation` — Azure Advisor Cost Recommendation

**Source collection:** `azure_advisor_recommendations`  
**Generated:** One document per Active recommendation

**Text template:**
```
Azure Advisor {impact} Impact Cost Recommendation (ID: {recommendation_id}):

Issue: {problem_description}
Recommended Action: {solution_description}
Affected Resource: {impacted_value} ({impacted_field.split('/')[-1]}) 
  in resource group '{resource_group}' (subscription: {subscription_id})

{If estimated_monthly_savings:
'Estimated monthly savings: ${estimated_monthly_savings:,.2f} {savings_currency}
 Estimated annual savings: ${estimated_annual_savings:,.2f} {savings_currency}'}

{If current_sku and recommended_sku:
'Current configuration: {current_sku}
 Recommended configuration: {recommended_sku}'}

Last updated by Advisor: {last_updated.strftime('%Y-%m-%d')}.
Status: {status}.
```

**Metadata:**
```python
{
    "period": None,
    "dimension": "AdvisorRecommendation",
    "dimension_value": impacted_value,
    "estimated_monthly_savings": 158.40,
    "currency": "USD",
    "recommendation_id": recommendation_id,
    "source_collection": "azure_advisor_recommendations"
}
```

---

### 2.4 `budget_status` — Budget Utilization Summary

**Source collection:** `azure_budgets`  
**Generated:** One document per budget

**Text template:**
```
Budget Status: '{budget_name}' ({time_grain} budget, scope: {scope})

Budget limit: ${amount:,.2f} {currency}
Current spend: ${current_spend:,.2f} ({utilization_pct:.1f}% utilized)
{If forecast_spend: 'Forecasted spend: ${forecast_spend:,.2f} ({forecast_utilization_pct:.1f}% of budget)'}

{If utilization_pct >= 100:
'CRITICAL: Budget has been EXCEEDED by ${current_spend - amount:,.2f}.'}
{elif utilization_pct >= 80:
'WARNING: Budget is {utilization_pct:.1f}% utilized — approaching limit.'}
{else:
'Budget is within normal utilization range.'}

Budget period: {time_period_start} to {time_period_end}.
```

**Metadata:**
```python
{
    "period": current_month,
    "dimension": "Budget",
    "dimension_value": budget_name,
    "total_cost": current_spend,
    "currency": currency,
    "source_collection": "azure_budgets"
}
```

---

### 2.5 `reservation_opportunity` — Reserved Instance Purchase Recommendation

**Source collection:** `azure_reservation_recommendations`  
**Generated:** One document per recommendation with `net_savings > 0`

**Text template:**
```
Reserved Instance Purchase Opportunity:

Resource type: {resource_type} ({sku_name}) in {location}
Look-back analysis: Based on {look_back_period} of usage data.
Term: {term_friendly} ({'1-year' if term == 'P1Y' else '3-year'} reserved instance)

Current pay-as-you-go monthly cost: ${monthly_paygo:,.2f} {currency}
Cost with {recommended_quantity} reserved instance(s): ${monthly_ri:,.2f} {currency}
Monthly savings: ${net_savings:,.2f} {currency} ({savings_pct:.1f}% reduction)
Annual savings: ${net_savings * 12:,.2f} {currency}

Recommendation: Purchase {recommended_quantity} {term_friendly} {sku_name} reserved instance(s) 
in {location} for the {scope} scope.
First usage detected: {first_usage_date}.
```

**Metadata:**
```python
{
    "period": current_month,
    "dimension": "ReservationOpportunity",
    "dimension_value": f"{sku_name}_{location}_{term}",
    "estimated_monthly_savings": net_savings,
    "currency": currency,
    "source_collection": "azure_reservation_recommendations"
}
```

---

### 2.6 `invoice_summary` — Invoice History Document

**Source collection:** `azure_invoices`  
**Generated:** One document per invoice

**Text template:**
```
Azure Invoice Summary:

Invoice ID: {invoice_id}
Billing period: {billing_period_start} to {billing_period_end}
Invoice date: {invoice_date}
{If due_date: 'Due date: {due_date}'}

Total charges: ${billed_amount:,.2f} {billing_currency}
{If azure_prepayment_applied > 0: 'Azure prepayment applied: ${azure_prepayment_applied:,.2f}'}
{If credit_amount > 0: 'Credits applied: ${credit_amount:,.2f}'}
Amount due: ${amount_due:,.2f} {billing_currency}

Payment status: {status}
{If status == 'PastDue': 'ALERT: This invoice is PAST DUE.'}
{If status == 'Paid': 'Invoice has been paid.'}
```

**Metadata:**
```python
{
    "period": billing_period_start[:7],  # "YYYY-MM"
    "dimension": "Invoice",
    "dimension_value": invoice_id,
    "total_cost": billed_amount,
    "currency": billing_currency,
    "source_collection": "azure_invoices"
}
```

---

### 2.7 `cost_anomaly` — Detected Cost Spike

**Source collection:** `azure_cost_details` (aggregated query)  
**Generated:** One document per service per period where daily cost exceeds 2x the 30-day average

**Detection query:**
For each service in the current billing period, compute:
- `avg_daily_cost` = average daily cost over the last 30 days
- `peak_daily_cost` = maximum daily cost in the current period
- If `peak_daily_cost > 2 * avg_daily_cost`, generate an anomaly document

**Text template:**
```
Cost Anomaly Detected:

Service: {service_name} in resource group '{resource_group}'
Anomaly date: {anomaly_date}
Anomaly cost: ${peak_daily_cost:,.2f} {currency} (on {anomaly_date})
30-day average daily cost: ${avg_daily_cost:,.2f} {currency}
Spike magnitude: {spike_multiple:.1f}x above average (${excess_cost:,.2f} above normal)

Potential causes to investigate:
- Unexpected resource scaling event
- Runaway compute job
- Misconfigured auto-scaling policy
- Data transfer spike

Resource details: {resource_name} ({resource_type}) in {location}.
Tags: {tags_formatted}.
```

**Metadata:**
```python
{
    "period": anomaly_date[:7],
    "dimension": "CostAnomaly",
    "dimension_value": f"{service_name}_{resource_group}",
    "total_cost": peak_daily_cost,
    "currency": currency,
    "source_collection": "azure_cost_details"
}
```

---

## 3. Embedding Pipeline

### 3.1 `generate_billing_documents(db, billing_period) -> list[dict]`

Queries MongoDB and generates all document types for the given period. Returns a list of dicts with keys: `document_type`, `text`, `metadata`, `source_ids`.

```
1. Query azure_cost_summary for billing_period → generate top_spenders + service_cost_detail documents
2. Query azure_advisor_recommendations where status="Active" → generate advisor_recommendation documents
3. Query azure_budgets → generate budget_status documents
4. Query azure_reservation_recommendations where net_savings > 0 → generate reservation_opportunity documents
5. Query azure_invoices where billing_period_start starts with billing_period → generate invoice_summary documents
6. Run anomaly detection on azure_cost_details for billing_period → generate cost_anomaly documents
Return combined list
```

### 3.2 `embed_documents(documents) -> list[dict]`

```python
async def embed_documents(documents: list[dict]) -> list[dict]:
    """Generates embeddings for a list of billing documents in batches of 100.
    
    Returns the same list with 'embedding' field populated on each dict.
    """
    client = _get_openai_client()
    BATCH_SIZE = 100
    
    for i in range(0, len(documents), BATCH_SIZE):
        batch = documents[i:i + BATCH_SIZE]
        texts = [doc["text"] for doc in batch]
        
        try:
            response = await client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=texts
            )
            for j, embedding_data in enumerate(response.data):
                batch[j]["embedding"] = embedding_data.embedding
        except Exception as e:
            raise VectorizerError(f"Embedding generation failed for batch {i // BATCH_SIZE}: {e}")
        
        # Respect rate limits between batches
        if i + BATCH_SIZE < len(documents):
            await asyncio.sleep(0.5)
    
    return documents
```

### 3.3 `upsert_vectors(db, documents) -> int`

```python
async def upsert_vectors(db: AsyncIOMotorDatabase, documents: list[dict]) -> int:
    """Upserts vectorized documents into azure_billing_vectors collection."""
    upserted = 0
    now = datetime.now(timezone.utc)
    
    for doc in documents:
        filter_key = {
            "document_type": doc["document_type"],
            "metadata.period": doc["metadata"].get("period"),
            "metadata.dimension_value": doc["metadata"].get("dimension_value")
        }
        update = {
            "$set": {
                **doc,
                "model": settings.OPENAI_EMBEDDING_MODEL,
                "dimensions": len(doc["embedding"]),
                "last_updated": now
            },
            "$setOnInsert": {"created_at": now}
        }
        result = await db["azure_billing_vectors"].update_one(filter_key, update, upsert=True)
        if result.upserted_id or result.modified_count:
            upserted += 1
    
    return upserted
```

### 3.4 `run_vectorization(db) -> int`

The top-level function called by the scheduler after each sync:

```python
async def run_vectorization(db: AsyncIOMotorDatabase) -> int:
    """Generates and upserts vector embeddings for the current and prior billing period."""
    from datetime import date
    
    today = date.today()
    current_period = today.strftime("%Y-%m")
    # Also vectorize prior period to catch late-arriving data updates
    prior_month = (today.replace(day=1) - timedelta(days=1))
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
```

---

## 4. Semantic Search

### 4.1 `semantic_search(db, query_text, document_types, period_filter, top_k) -> list[dict]`

```python
async def semantic_search(
    db: AsyncIOMotorDatabase,
    query_text: str,
    document_types: list[str] | None = None,
    period_filter: str | None = None,
    top_k: int = 10
) -> list[dict]:
    """Embeds query_text and returns top_k most semantically similar billing documents."""
    client = _get_openai_client()
    
    # Embed the query
    response = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=[query_text]
    )
    query_embedding = response.data[0].embedding
    
    # Build vector search pipeline
    vector_search_stage = {
        "$vectorSearch": {
            "index": "billing_vector_index",
            "path": "embedding",
            "queryVector": query_embedding,
            "numCandidates": top_k * 10,
            "limit": top_k,
        }
    }
    
    # Add filters if specified
    if document_types or period_filter:
        filter_conditions = {}
        if document_types:
            filter_conditions["document_type"] = {"$in": document_types}
        if period_filter:
            filter_conditions["metadata.period"] = period_filter
        vector_search_stage["$vectorSearch"]["filter"] = filter_conditions
    
    pipeline = [
        vector_search_stage,
        {
            "$project": {
                "embedding": 0,  # Exclude embedding vector from results (large)
                "score": {"$meta": "vectorSearchScore"},
                "document_type": 1,
                "text": 1,
                "metadata": 1
            }
        }
    ]
    
    results = await db["azure_billing_vectors"].aggregate(pipeline).to_list(length=top_k)
    return results
```

---

## 5. AI Query Endpoint Processing

### 5.1 System Prompt

```python
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
```

### 5.2 Endpoint Processing Logic

```python
@billing_router.post("/ai/query", response_model=BillingAIQueryResponse)
async def billing_ai_query(
    request: BillingAIQueryRequest,
    db=Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Processes a natural language cost analysis query using semantic search and LLM synthesis."""
    try:
        # 1. Retrieve relevant context documents via vector search
        relevant_docs = await vectorizer.semantic_search(
            db=db,
            query_text=request.question,
            document_types=request.document_types,
            period_filter=request.period_filter,
            top_k=request.top_k
        )
        
        if not relevant_docs:
            return BillingAIQueryResponse(
                answer="No billing data is available for analysis yet. Please trigger a billing sync first via POST /api/billing/sync/trigger.",
                sources=[],
                model=settings.OPENAI_CHAT_MODEL,
                question=request.question
            )
        
        # 2. Format context from retrieved documents
        context_parts = []
        for i, doc in enumerate(relevant_docs, 1):
            context_parts.append(f"[{i}] {doc['document_type'].upper()} (relevance: {doc.get('score', 0):.3f}):\n{doc['text']}")
        context_text = "\n\n---\n\n".join(context_parts)
        
        # 3. Call LLM
        client = vectorizer._get_openai_client()
        chat_response = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": BILLING_AI_SYSTEM_PROMPT.format(context=context_text)
                },
                {
                    "role": "user",
                    "content": request.question
                }
            ],
            temperature=0.2,  # Low temperature for factual cost analysis
            max_tokens=1500
        )
        
        answer = chat_response.choices[0].message.content
        
        # 4. Build source citations
        sources = [
            AIQuerySource(
                document_type=doc["document_type"],
                period=doc["metadata"].get("period"),
                dimension_value=doc["metadata"].get("dimension_value"),
                total_cost=doc["metadata"].get("total_cost"),
                score=doc.get("score", 0.0)
            )
            for doc in relevant_docs
        ]
        
        return BillingAIQueryResponse(
            answer=answer,
            sources=sources,
            model=settings.OPENAI_CHAT_MODEL,
            question=request.question
        )
    
    except VectorizerError as e:
        logger.error(f"AI query vectorizer error: {e}")
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {e}")
    except Exception as e:
        logger.error(f"AI query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to process AI query.")
```

---

## 6. Testing Strategy for Vectorizer

### Mock objects needed:

```python
# In conftest.py additions:

@pytest.fixture
def mock_openai_embeddings():
    """Returns a mock OpenAI embedding response with a 1536-dim zero vector."""
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.0] * 1536)]
    return mock_response

@pytest.fixture
def mock_openai_chat():
    """Returns a mock OpenAI chat completion response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Mock cost analysis answer."))]
    return mock_response
```

### Tests to write (`test_billing_vectorizer.py`):

1. **`test_generate_billing_documents_empty_db`** — verify empty list returned when collections are empty
2. **`test_generate_top_spenders_document`** — mock cost_summary docs, verify text template output
3. **`test_generate_advisor_recommendation_document`** — mock advisor doc, verify text
4. **`test_embed_documents_batching`** — verify 250 docs results in 3 OpenAI API calls (batches of 100)
5. **`test_embed_documents_rate_limit_sleep`** — verify `asyncio.sleep(0.5)` is called between batches
6. **`test_upsert_vectors_idempotency`** — call upsert twice with same docs, verify record count unchanged
7. **`test_semantic_search_builds_correct_pipeline`** — verify `$vectorSearch` stage is correct
8. **`test_semantic_search_with_filters`** — verify filter conditions are appended when document_types provided
9. **`test_ai_query_endpoint_success`** — mock semantic_search and openai, verify 200 response
10. **`test_ai_query_endpoint_no_data`** — mock empty semantic_search, verify informative message
