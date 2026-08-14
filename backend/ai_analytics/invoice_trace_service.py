"""Invoice trace service — forensic per-claim timeline view.

Combines all available SQL and Mongo data for a single claim into a
comprehensive trace: business outcome, AI processing, conversations,
line items, and comparison.

Phase 9 note: This service is NOT affected by
``settings.AI_ANALYTICS_USE_PROJECTION``. It reads the raw
``ai_line_items`` document directly from RecoveryHub_AI Mongo because the
forensic trace needs the full ``line_items`` array (with nested
``resources``), the raw ``review_msg``, and the full conversation
documents — none of which are in the worker's ``ai_invoice_analytics``
projection (Section 9 only stores a line-item summary and conversation
counts). Phase 10 will enrich the projection to carry the data needed
to replace this direct-read path.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import AiInvoiceTrace, AiConversationRecord, AiLineItemEntry, AiFinalLineItemEntry, AiLineItemComparison
from . import sql_repository as sql_repo
from . import mongo_repository as mongo_repo
from .normalization import (
    classify_business_outcome,
    classify_writeback_status,
    calculate_retry_count,
    detect_human_intervention,
    RELEASED_LOG_TEXT,
    CANCELLED_LOG_TEXT,
)
from .reason_normalization import normalize_reason

logger = logging.getLogger(__name__)


def _serialize_datetime(val: Any) -> Optional[str]:
    """Convert a datetime to ISO string, or None."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _extract_ai_line_items(ai_record: Dict[str, Any]) -> List[AiLineItemEntry]:
    """Extract AI-generated line items from the ai_line_items document.

    The ``line_items`` field can be a list of dicts or None. Each entry
    typically has: item/label, description, quantity, rate/amount, and
    nested resources.
    """
    raw_items = ai_record.get("line_items")
    if not raw_items or not isinstance(raw_items, list):
        return []

    entries: List[AiLineItemEntry] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        entries.append(AiLineItemEntry(
            item=item.get("item") or item.get("label") or item.get("name"),
            description=item.get("description"),
            quantity=float(item["quantity"]) if item.get("quantity") is not None else None,
            rate=float(item["rate"]) if item.get("rate") is not None else
                 (float(item["amount"]) if item.get("amount") is not None else None),
            line_item_total=float(item["total"]) if item.get("total") is not None else
                           (float(item["line_item_total"]) if item.get("line_item_total") is not None else None),
            resources=item.get("resources", []) if isinstance(item.get("resources"), list) else [],
        ))
    return entries


def _extract_final_line_items(
    claim_services: List[Dict[str, Any]],
    resource_mappings: List[Dict[str, Any]],
) -> List[AiFinalLineItemEntry]:
    """Build final RH line items from claim_services + resource fee mappings."""
    # Index resource mappings by ClaimServiceId
    resources_by_service: Dict[int, List[Dict[str, Any]]] = {}
    for rm in resource_mappings:
        service_id = rm.get("ClaimServiceId")
        if service_id is not None:
            resources_by_service.setdefault(int(service_id), []).append({
                "resourceLabel": rm.get("resourceLabel"),
                "quantity": float(rm["Quantity"]) if rm.get("Quantity") is not None else None,
                "amount": float(rm["Amount"]) if rm.get("Amount") is not None else None,
                "unit": float(rm["unit"]) if rm.get("unit") is not None else None,
            })

    entries: List[AiFinalLineItemEntry] = []
    for cs in claim_services:
        service_id = cs.get("id")
        if service_id is None:
            continue
        entries.append(AiFinalLineItemEntry(
            claim_service_id=int(service_id),
            item=cs.get("item"),
            rate=float(cs["rate"]) if cs.get("rate") is not None else None,
            quantity=float(cs["quantity"]) if cs.get("quantity") is not None else None,
            description=cs.get("description"),
            resources=resources_by_service.get(int(service_id), []),
        ))
    return entries


def _build_comparison(
    ai_items: List[AiLineItemEntry],
    final_items: List[AiFinalLineItemEntry],
) -> AiLineItemComparison:
    """Compare AI-generated line items with final RH line items."""
    ai_total = sum(i.line_item_total or (i.rate or 0) * (i.quantity or 0) for i in ai_items)
    final_total = sum(i.rate or 0 * i.quantity or 0 for i in final_items)
    # Fix: proper multiplication
    final_total = sum((i.rate or 0) * (i.quantity or 0) for i in final_items)

    ai_item_names = {i.item for i in ai_items if i.item}
    final_item_names = {i.item for i in final_items if i.item}

    ai_only = list(ai_item_names - final_item_names)
    rh_only = list(final_item_names - ai_item_names)

    # Quantity changes
    qty_changes: List[Dict[str, Any]] = []
    rate_changes: List[Dict[str, Any]] = []
    for ai_item in ai_items:
        if not ai_item.item:
            continue
        for final_item in final_items:
            if final_item.item == ai_item.item:
                if ai_item.quantity is not None and final_item.quantity is not None:
                    if abs(ai_item.quantity - final_item.quantity) > 0.01:
                        qty_changes.append({
                            "item": ai_item.item,
                            "ai_quantity": ai_item.quantity,
                            "final_quantity": final_item.quantity,
                        })
                if ai_item.rate is not None and final_item.rate is not None:
                    if abs(ai_item.rate - final_item.rate) > 0.01:
                        rate_changes.append({
                            "item": ai_item.item,
                            "ai_rate": ai_item.rate,
                            "final_rate": final_item.rate,
                        })
                break

    return AiLineItemComparison(
        ai_original_amount=round(ai_total, 2) if ai_total else None,
        final_rh_amount=round(final_total, 2) if final_total else None,
        difference=round(final_total - ai_total, 2) if (ai_total or final_total) else None,
        ai_only_items=ai_only,
        rh_only_items=rh_only,
        quantity_changes=qty_changes,
        rate_changes=rate_changes,
    )


async def get_invoice_trace(ai_db, claim_id: int) -> AiInvoiceTrace:
    """Build a comprehensive forensic trace for a single claim.

    Fetches data from:
    - SQL: AIInvoiceProcessRHTemp, Claims, Departments, ai_claims_process_logs,
      AIClaimInvoiceCancellationDetails, claim_services, Claim_Service_ResourceFeeMapping
    - Mongo: ai_line_items, ai_agent_conversations
    """
    source_status: Dict[str, str] = {
        "recoveryhub_sql": "available",
        "recoveryhub_ai_mongo": "available",
    }
    data_complete = True

    # 1. SQL: Get the AI invoice process record + claim + department (single claim)
    sql_row = sql_repo.get_ai_invoice_record_for_claim(claim_id)

    if sql_row is None:
        # Claim not in AIInvoiceProcessRHTemp — return minimal trace
        return AiInvoiceTrace(
            claim_id=claim_id,
            business_outcome="unknown",
            ai_record_state="missing",
            business_record_state="missing",
            source_status={"recoveryhub_sql": "claim_not_found"},
            data_complete=False,
        )

    ai_inv_process_status = sql_row.get("AI_inv_process_status") or sql_row.get("ai_inv_process_status")

    # 2. SQL: Process logs
    process_logs = sql_repo.get_process_logs_for_claim(claim_id)

    # 3. SQL: Cancellation details
    cancellations = sql_repo.get_cancellation_details_for_claim(claim_id)
    cancellation = cancellations[0] if cancellations else None

    # 4. Classify business outcome
    has_released = any(log.get("log_text") == RELEASED_LOG_TEXT for log in process_logs)
    has_cancelled = any(log.get("log_text") == CANCELLED_LOG_TEXT for log in process_logs)
    has_cancellation_record = cancellation is not None

    outcome = classify_business_outcome(
        ai_inv_process_status=ai_inv_process_status,
        has_released_log=has_released,
        has_cancelled_log=has_cancelled,
        has_cancellation_record=has_cancellation_record,
    )

    # 5. Mongo: AI line items
    ai_record = None
    try:
        ai_record = await mongo_repo.get_ai_line_items_for_claim(ai_db, claim_id)
    except Exception as e:
        logger.error(f"Failed to fetch ai_line_items for claim {claim_id}: {e}")
        source_status["recoveryhub_ai_mongo"] = "unavailable"
        data_complete = False

    # 6. Mongo: Agent conversations
    conversations: List[AiConversationRecord] = []
    try:
        conv_docs = await mongo_repo.get_agent_conversations_for_claim(ai_db, claim_id)
        for doc in conv_docs:
            conversations.append(AiConversationRecord(
                conversation_id=str(doc.get("_id", "")),
                agent=doc.get("agent"),
                status=doc.get("status"),
                created_at=_serialize_datetime(doc.get("created_at")),
                processing_stage=doc.get("processing_stage"),
                request_type=doc.get("request_type"),
                execution_time_seconds=doc.get("execution_time_seconds"),
                input_data=doc.get("input_data"),
                incident_json=doc.get("incident_json"),
                results=doc.get("results"),
                output_data=doc.get("output_data"),
            ))
    except Exception as e:
        logger.error(f"Failed to fetch conversations for claim {claim_id}: {e}")

    # 7. SQL: Final line items
    final_line_items: List[AiFinalLineItemEntry] = []
    try:
        claim_services = sql_repo.get_final_line_items(claim_id)
        resource_mappings = sql_repo.get_final_resource_line_items(claim_id)
        final_line_items = _extract_final_line_items(claim_services, resource_mappings)
    except Exception as e:
        logger.error(f"Failed to fetch final line items for claim {claim_id}: {e}")

    # 8. Extract AI line items
    ai_line_items: List[AiLineItemEntry] = []
    if ai_record:
        ai_line_items = _extract_ai_line_items(ai_record)

    # 9. Build comparison
    comparison = _build_comparison(ai_line_items, final_line_items)

    # 9a. Sanitize the raw AI record for JSON serialization.
    # MongoDB documents contain BSON-specific types (ObjectId, datetime, etc.)
    # that Pydantic cannot serialize to JSON. We convert _id to a string and
    # drop any other non-serializable values so the raw_ai_record field can
    # be safely returned in the API response.
    raw_ai_record_sanitized: Optional[Dict[str, Any]] = None
    if ai_record:
        raw_ai_record_sanitized = {}
        for k, v in ai_record.items():
            if k == "_id":
                raw_ai_record_sanitized[k] = str(v)
            elif hasattr(v, "isoformat"):
                raw_ai_record_sanitized[k] = v.isoformat()
            elif isinstance(v, (list, dict)):
                # Best-effort: keep as-is; nested BSON types are rare in
                # the fields the frontend actually displays.
                raw_ai_record_sanitized[k] = v
            elif isinstance(v, (str, int, float, bool, type(None))):
                raw_ai_record_sanitized[k] = v
            else:
                raw_ai_record_sanitized[k] = str(v)

    # 10. Normalize rejection reason
    raw_rejection_reason = None
    raw_rejection_descr = None
    normalized_category = None
    if cancellation:
        reason_id = cancellation.get("reason_id")
        raw_rejection_reason = cancellation.get("raw_reason") or cancellation.get("reason")
        raw_rejection_descr = cancellation.get("reason_descr") or cancellation.get("reason_description")
        normalized = normalize_reason(reason_id, raw_rejection_reason, raw_rejection_descr)
        normalized_category = normalized["normalized_category"]

    # 11. Build the trace
    writeback = classify_writeback_status(
        ai_record.get("line_items_save_to_rh_status") if ai_record else None,
        ai_record.get("claim_processing_status") if ai_record else None,
    )
    retry = calculate_retry_count(
        ai_record=ai_record,
        retry_thread_id=ai_record.get("retry_thread_id") if ai_record else None,
        agent_exec_status=ai_record.get("agent_exec_status") if ai_record else None,
    )

    # Process logs for the trace (serialized)
    trace_logs = [
        {
            "id": log.get("id"),
            "log_text": log.get("log_text"),
            "user_id": log.get("user_id"),
            "user_type_id": log.get("user_type_id"),
            "created_date": _serialize_datetime(log.get("created_date")),
        }
        for log in process_logs
    ]

    return AiInvoiceTrace(
        claim_id=claim_id,
        invoice_number=sql_row.get("invoice_number"),
        run_number=sql_row.get("run_number"),
        department_id=sql_row.get("dept_id"),
        department_name=sql_row.get("department_name"),
        claim_created_at=_serialize_datetime(sql_row.get("claim_created_at")),
        alarm_received=sql_row.get("alarm_received"),
        call_cleared=sql_row.get("call_cleared"),
        recoveryhub_claim_status=sql_row.get("recoveryhub_claim_status"),
        business_outcome=outcome,
        ai_inv_process_status=ai_inv_process_status,
        business_status_updated_at=_serialize_datetime(sql_row.get("ai_business_updated_at")),
        process_logs=trace_logs,
        cancellation_reason=raw_rejection_reason,
        cancellation_description=raw_rejection_descr,
        cancellation_date=_serialize_datetime(cancellation.get("date_of_cancellation")) if cancellation else None,
        business_user_id=next((log.get("user_id") for log in process_logs if log.get("log_text") in (RELEASED_LOG_TEXT, CANCELLED_LOG_TEXT)), None),
        ai_record_state="present" if ai_record else "missing",
        claim_processing_status=ai_record.get("claim_processing_status") if ai_record else None,
        agent_exec_status=ai_record.get("agent_exec_status") if ai_record else None,
        inserted_at=_serialize_datetime(ai_record.get("inserted_at")) if ai_record else None,
        updated_at=_serialize_datetime(ai_record.get("updated_at")) if ai_record else None,
        completed_at=_serialize_datetime(ai_record.get("completed_at")) if ai_record else None,
        billing_category=ai_record.get("billing_category") if ai_record else None,
        incident_duration_in_minutes=ai_record.get("incident_duration_in_minutes") if ai_record else None,
        confidence_level=ai_record.get("confidence_level") if ai_record else None,
        review_msg=ai_record.get("review_msg") if ai_record else None,
        line_items_save_to_rh_status=ai_record.get("line_items_save_to_rh_status") if ai_record else None,
        invoice_total=ai_record.get("invoice_total") if ai_record else None,
        processing_time_seconds=ai_record.get("processing_time_seconds") if ai_record else None,
        retry_count=retry,
        conversation_id=str(ai_record.get("conversation_id")) if ai_record and ai_record.get("conversation_id") else None,
        thread_id_is_billable=ai_record.get("thread_id_is_billable") if ai_record else None,
        thread_id=ai_record.get("thread_id") if ai_record else None,
        retry_thread_id=ai_record.get("retry_thread_id") if ai_record else None,
        conversations=conversations,
        ai_line_items=ai_line_items,
        final_line_items=final_line_items,
        comparison=comparison,
        raw_ai_record=raw_ai_record_sanitized,
        source_status=source_status,
        data_complete=data_complete,
    )
