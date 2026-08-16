"""Service-level tests for Phase 10 Invoice Trace projection integration.

Feature under test: the flag-gated Invoice Trace read path.
Failure prevented: projection data being mistaken for a present AI record,
projection-only conversation summaries being ignored, or metadata leaking into
``raw_ai_record``. Test level: unit/service with mocked SQL and Mongo.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def sql_row():
    return {
        "invoice_number": "INV-100",
        "run_number": "RUN-100",
        "dept_id": 1,
        "department_name": "FD1",
        "claim_created_at": None,
        "alarm_received": None,
        "call_cleared": None,
        "recoveryhub_claim_status": "Ready",
        "AI_inv_process_status": 4,
        "ai_business_updated_at": None,
    }


@pytest.mark.asyncio
async def test_trace_projection_missing_ai_record_is_not_present(sql_row, monkeypatch):
    """An explicit missing-source projection must report ai_record_state=missing."""
    from ai_analytics.invoice_trace_service import get_invoice_trace
    from config import settings
    from database import db_manager

    monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)
    monkeypatch.setattr(db_manager, "db", object())

    with patch("ai_analytics.invoice_trace_service.sql_repo.get_ai_invoice_record_for_claim", return_value=sql_row), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_process_logs_for_claim", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_cancellation_details_for_claim", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_final_line_items", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_final_resource_line_items", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.projection_repo.get_projection_for_trace", new_callable=AsyncMock, return_value={
             "has_ai_line_item_record": False,
             "conversation_summaries": [],
         }), \
         patch("ai_analytics.invoice_trace_service.mongo_repo.get_ai_line_items_for_claim", new_callable=AsyncMock) as raw_ai, \
         patch("ai_analytics.invoice_trace_service.mongo_repo.get_agent_conversations_for_claim", new_callable=AsyncMock, return_value=[]):
        trace = await get_invoice_trace(object(), 100)

    assert trace.ai_record_state == "missing"
    assert trace.raw_ai_record is None
    raw_ai.assert_not_awaited()


@pytest.mark.asyncio
async def test_trace_uses_projection_summary_when_conversation_read_fails(sql_row, monkeypatch):
    """Projection summaries keep the trace useful when source conversation detail is unavailable."""
    from ai_analytics.invoice_trace_service import get_invoice_trace
    from config import settings
    from database import db_manager

    monkeypatch.setattr(settings, "AI_ANALYTICS_USE_PROJECTION", True)
    monkeypatch.setattr(db_manager, "db", object())
    projection = {
        "has_ai_line_item_record": True,
        "claim_processing_status": "COMPLETED",
        "agent_exec_status": "success",
        "line_items": [{"item": "Equipment", "rate": 10, "line_item_total": 20, "resources": []}],
        "conversation_summaries": [{
            "conversation_id": "conv-1",
            "agent": "agent-a",
            "status": "completed",
            "created_at": "2026-07-01T09:00:00",
            "processing_stage": "stage-1",
            "request_type": "incident_analysis",
        }],
    }

    with patch("ai_analytics.invoice_trace_service.sql_repo.get_ai_invoice_record_for_claim", return_value=sql_row), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_process_logs_for_claim", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_cancellation_details_for_claim", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_final_line_items", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.sql_repo.get_final_resource_line_items", return_value=[]), \
         patch("ai_analytics.invoice_trace_service.projection_repo.get_projection_for_trace", new_callable=AsyncMock, return_value=projection), \
         patch("ai_analytics.invoice_trace_service.mongo_repo.get_agent_conversations_for_claim", new_callable=AsyncMock, side_effect=RuntimeError("source unavailable")):
        trace = await get_invoice_trace(object(), 100)

    assert trace.ai_record_state == "present"
    assert trace.ai_line_items[0].item == "Equipment"
    assert trace.ai_line_items[0].rate == 10
    assert len(trace.conversations) == 1
    assert trace.conversations[0].conversation_id == "conv-1"
    assert trace.conversations[0].input_data is None
    assert trace.raw_ai_record is not None
    assert "conversation_summaries" not in trace.raw_ai_record
    assert "has_ai_line_item_record" not in trace.raw_ai_record
    assert trace.data_complete is False
