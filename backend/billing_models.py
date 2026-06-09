"""Pydantic v2 models for the Azure billing analytics API boundary.

Mirrors the response/request shapes defined in Supporting Doc 05, Section 1.
All new code uses ``str | None`` union syntax (Python 3.11) per the project
conventions.
"""
from datetime import datetime

from pydantic import BaseModel, Field


# --- Request Models ---

class BillingSyncRequest(BaseModel):
    """Payload to manually trigger a billing sync."""
    sync_type: str = Field(..., description="Type of sync: full, daily, advisor, invoices, reservations, resource_inventory, retail_prices, vectorize")
    billing_period: str | None = Field(None, description="YYYY-MM. Required for period-specific syncs. Ignored for full sync.")


class BillingAIQueryRequest(BaseModel):
    """Payload for a natural language cost analysis query."""
    question: str = Field(..., min_length=5, max_length=1000, description="Natural language question about Azure costs")
    document_types: list[str] | None = Field(None, description="Filter to specific document types. None = all types.")
    period_filter: str | None = Field(None, description="YYYY-MM period to focus on. None = all periods.")
    top_k: int = Field(10, ge=1, le=50, description="Number of context documents to retrieve")


# --- Response Models ---

class SyncStatusEntry(BaseModel):
    sync_type: str
    status: str
    last_run: datetime | None
    last_period: str | None
    records_synced: int
    duration_seconds: float | None
    error_message: str | None


class SyncStatusResponse(BaseModel):
    syncs: list[SyncStatusEntry]


class CostSummaryItem(BaseModel):
    period: str
    dimension: str
    dimension_value: str
    total_cost: float
    currency: str
    change_pct: float | None
    change_amount: float | None
    record_count: int


class CostSummaryResponse(BaseModel):
    items: list[CostSummaryItem]
    total: float
    currency: str
    period: str


class AdvisorRecommendationItem(BaseModel):
    recommendation_id: str
    category: str
    impact: str
    impacted_value: str
    resource_group: str
    problem_description: str
    solution_description: str
    estimated_monthly_savings: float | None
    savings_currency: str | None
    current_sku: str | None
    recommended_sku: str | None
    last_updated: datetime
    status: str


class AdvisorSummaryResponse(BaseModel):
    total_recommendations: int
    cost_recommendations: int
    total_monthly_savings: float
    currency: str
    by_impact: dict[str, int]


class InvoiceItem(BaseModel):
    invoice_id: str
    billing_period_start: str
    billing_period_end: str
    invoice_date: str
    due_date: str | None
    billed_amount: float
    amount_due: float
    billing_currency: str
    status: str
    invoice_download_url: str | None


class BudgetItem(BaseModel):
    budget_name: str
    scope: str
    amount: float
    current_spend: float
    forecast_spend: float | None
    utilization_pct: float
    time_grain: str
    currency: str


class AIQuerySource(BaseModel):
    document_type: str
    period: str | None
    dimension_value: str | None
    total_cost: float | None
    score: float


class BillingAIQueryResponse(BaseModel):
    answer: str
    sources: list[AIQuerySource]
    model: str
    question: str
