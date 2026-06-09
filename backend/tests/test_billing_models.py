from datetime import datetime

import pytest

from billing_models import (
    BillingSyncRequest,
    BillingAIQueryRequest,
    CostSummaryItem,
    AdvisorRecommendationItem,
)


def test_billing_sync_request_defaults():
    req = BillingSyncRequest(sync_type="daily")
    assert req.sync_type == "daily"
    assert req.billing_period is None


def test_ai_query_request_validation():
    req = BillingAIQueryRequest(question="What are my top spending services?")
    assert req.top_k == 10
    assert req.document_types is None

    with pytest.raises(ValueError):
        BillingAIQueryRequest(question="hi")  # below min_length

    with pytest.raises(ValueError):
        BillingAIQueryRequest(question="valid question", top_k=99)  # above le=50


def test_cost_summary_item_optional_change_fields():
    item = CostSummaryItem(
        period="2026-05",
        dimension="ServiceName",
        dimension_value="Virtual Machines",
        total_cost=1234.56,
        currency="USD",
        change_pct=None,
        change_amount=None,
        record_count=42,
    )
    assert item.total_cost == 1234.56
    assert item.change_pct is None


def test_advisor_recommendation_item_requires_core_fields():
    rec = AdvisorRecommendationItem(
        recommendation_id="rec-1",
        category="Cost",
        impact="High",
        impacted_value="vm-01",
        resource_group="rg-prod",
        problem_description="Underutilized VM",
        solution_description="Resize or shut down",
        estimated_monthly_savings=50.0,
        savings_currency="USD",
        current_sku="Standard_D4s_v3",
        recommended_sku="Standard_D2s_v3",
        last_updated=datetime.utcnow(),
        status="Active",
    )
    assert rec.estimated_monthly_savings == 50.0
