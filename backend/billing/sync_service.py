"""Sync orchestration: reads Azure APIs via billing modules, writes to MongoDB.

All MongoDB writes use upserts keyed on the dedup keys defined in Doc 04 so syncs
are idempotent. Each sync run is recorded in ``azure_billing_sync_log``.
"""
import asyncio
import calendar
import json
import logging
from datetime import datetime, timezone

from bson import ObjectId

from config import settings
from billing import advisor, billing_accounts, consumption, cost_management, resource_graph, retail_prices

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Small parsing/coercion helpers
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_since(started) -> float | None:
    """Seconds between `started` and now, tolerant of naive datetimes from the DB driver."""
    if not started:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (_now() - started).total_seconds()


def _scope() -> str:
    """Default subscription scope from settings."""
    return f"/subscriptions/{settings.AZURE_SUBSCRIPTION_ID}"


def _period_dates(billing_period: str) -> tuple[str, str]:
    """Returns (start, end) ISO dates (YYYY-MM-DD) for a 'YYYY-MM' billing period."""
    year, month = (int(p) for p in billing_period.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return f"{billing_period}-01", f"{billing_period}-{last_day:02d}"


def _recent_periods(months: int) -> list[str]:
    """Returns the last `months` billing periods as 'YYYY-MM', oldest first."""
    now = _now()
    periods: list[str] = []
    year, month = now.year, now.month
    for _ in range(months):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(periods))


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _parse_json_field(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _resource_name(resource_id: str) -> str:
    return resource_id.rstrip("/").split("/")[-1] if resource_id else ""


# --------------------------------------------------------------------------- #
# Sync log helpers
# --------------------------------------------------------------------------- #

async def _write_sync_log_start(db, sync_type: str, billing_period: str | None, triggered_by: str) -> str:
    doc = {
        "sync_type": sync_type,
        "status": "running",
        "billing_period": billing_period,
        "started_at": _now(),
        "completed_at": None,
        "records_synced": 0,
        "records_skipped": 0,
        "error_message": None,
        "duration_seconds": None,
        "triggered_by": triggered_by,
    }
    result = await db["azure_billing_sync_log"].insert_one(doc)
    return str(result.inserted_id)


async def _write_sync_log_complete(db, log_id: str, records_synced: int, records_skipped: int = 0) -> None:
    log = await db["azure_billing_sync_log"].find_one({"_id": ObjectId(log_id)})
    duration = _duration_since(log.get("started_at") if log else None)
    await db["azure_billing_sync_log"].update_one(
        {"_id": ObjectId(log_id)},
        {"$set": {
            "status": "completed",
            "completed_at": _now(),
            "records_synced": records_synced,
            "records_skipped": records_skipped,
            "duration_seconds": duration,
        }},
    )


async def _write_sync_log_failed(db, log_id: str, error_message: str) -> None:
    log = await db["azure_billing_sync_log"].find_one({"_id": ObjectId(log_id)})
    duration = _duration_since(log.get("started_at") if log else None)
    await db["azure_billing_sync_log"].update_one(
        {"_id": ObjectId(log_id)},
        {"$set": {
            "status": "failed",
            "completed_at": _now(),
            "error_message": error_message,
            "duration_seconds": duration,
        }},
    )


# --------------------------------------------------------------------------- #
# Cost details
# --------------------------------------------------------------------------- #

def _clean_cost_row(row: dict, billing_period: str) -> dict:
    """Maps a raw CSV cost-detail row to the azure_cost_details schema."""
    resource_id = row.get("ResourceId", "") or ""
    date_raw = row.get("Date", "") or ""
    return {
        "billing_period": billing_period,
        "date": date_raw,
        "subscription_id": row.get("SubscriptionId", ""),
        "subscription_name": row.get("SubscriptionName", ""),
        "resource_group": row.get("ResourceGroupName", ""),
        "resource_id": resource_id,
        "resource_name": _resource_name(resource_id),
        "service_name": row.get("MeterCategory", ""),
        "service_family": row.get("ServiceFamily", ""),
        "meter_category": row.get("MeterCategory", ""),
        "meter_subcategory": row.get("MeterSubCategory", ""),
        "meter_name": row.get("MeterName", ""),
        "meter_id": row.get("MeterId", ""),
        "quantity": _to_float(row.get("Quantity")),
        "unit_of_measure": row.get("UnitOfMeasure", ""),
        "unit_price": _to_float(row.get("UnitPrice")),
        "effective_price": _to_float(row.get("EffectivePrice")),
        "pre_tax_cost": _to_float(row.get("Cost")),
        "pay_go_price": _to_float(row.get("PayGPrice")),
        "billing_currency": row.get("BillingCurrency", ""),
        "tags": _parse_json_field(row.get("Tags")),
        "location": row.get("ResourceLocation", ""),
        "consumed_service": row.get("ConsumedService", ""),
        "charge_type": row.get("ChargeType", ""),
        "publisher_name": row.get("PublisherName", ""),
        "publisher_type": row.get("PublisherType", ""),
        "pricing_model": row.get("PricingModel", ""),
        "reservation_id": row.get("ReservationId") or None,
        "reservation_name": row.get("ReservationName") or None,
        "benefit_id": row.get("Benefitid") or None,
        "benefit_name": row.get("BenefitName") or None,
        "invoice_id": row.get("InvoiceId") or None,
        "is_azure_credit_eligible": _to_bool(row.get("IsAzureCreditEligible")),
        "frequency": row.get("Frequency", ""),
        "term": row.get("Term") or None,
        "product_name": row.get("Product", ""),
        "part_number": row.get("PartNumber", ""),
        "sku_id": row.get("SkuId") or None,
        "plan_name": row.get("PlanName") or None,
        "additional_info": _parse_json_field(row.get("AdditionalInfo")),
        "sync_timestamp": _now(),
        "data_source": "cost_details_api",
    }


async def _upsert_cost_row(db, record: dict) -> None:
    key = {
        "subscription_id": record["subscription_id"],
        "date": record["date"],
        "resource_id": record["resource_id"],
        "meter_id": record["meter_id"],
        "charge_type": record["charge_type"],
    }
    await db["azure_cost_details"].update_one(key, {"$set": record}, upsert=True)


async def _rebuild_cost_summary(db, billing_period: str) -> None:
    """Aggregates azure_cost_details by dimension and upserts azure_cost_summary."""
    dimensions = {
        "ServiceName": "service_name",
        "ResourceGroupName": "resource_group",
        "Location": "location",
        "ChargeType": "charge_type",
    }
    cursor = db["azure_cost_details"].find({"billing_period": billing_period})
    rows = await cursor.to_list(length=None)

    for dimension, field in dimensions.items():
        buckets: dict[str, dict] = {}
        for row in rows:
            value = row.get(field) or "Unknown"
            bucket = buckets.setdefault(value, {"total_cost": 0.0, "usage_quantity": 0.0, "count": 0, "currency": row.get("billing_currency", "USD")})
            bucket["total_cost"] += _to_float(row.get("pre_tax_cost"))
            bucket["usage_quantity"] += _to_float(row.get("quantity"))
            bucket["count"] += 1

        for value, bucket in buckets.items():
            key = {
                "period": billing_period,
                "subscription_id": settings.AZURE_SUBSCRIPTION_ID,
                "dimension": dimension,
                "dimension_value": value,
            }
            summary = {
                **key,
                "total_cost": round(bucket["total_cost"], 4),
                "currency": bucket["currency"],
                "usage_quantity": round(bucket["usage_quantity"], 4),
                "unit_of_measure": "",
                "record_count": bucket["count"],
                "sync_timestamp": _now(),
            }
            await db["azure_cost_summary"].update_one(key, {"$set": summary}, upsert=True)


async def sync_cost_details(db, billing_period: str, triggered_by: str = "manual_api") -> int:
    """Syncs unaggregated cost line items for one billing period, then rebuilds summaries."""
    log_id = await _write_sync_log_start(db, "cost_details_daily", billing_period, triggered_by)
    try:
        scope = _scope()
        start, end = _period_dates(billing_period)
        count = 0
        for metric in ("ActualCost", "AmortizedCost"):
            rows = await cost_management.generate_cost_details_report(scope, start, end, metric)
            for row in rows:
                await _upsert_cost_row(db, _clean_cost_row(row, billing_period))
                count += 1
        await _rebuild_cost_summary(db, billing_period)
        await _write_sync_log_complete(db, log_id, count)
        return count
    except Exception as exc:  # noqa: BLE001 — log and record failure, then re-raise
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Advisor
# --------------------------------------------------------------------------- #

def _map_advisor(rec: dict) -> dict:
    props = rec.get("properties", {})
    ext = props.get("extendedProperties", {})
    short = props.get("shortDescription", {})
    monthly, currency = advisor._extract_savings(ext)
    annual = monthly * 12 if monthly is not None else None
    resource_id = props.get("resourceMetadata", {}).get("resourceId", "")
    return {
        "recommendation_id": rec.get("name", ""),
        "recommendation_arm_id": rec.get("id", ""),
        "category": props.get("category", ""),
        "impact": props.get("impact", ""),
        "impacted_field": props.get("impactedField", ""),
        "impacted_value": props.get("impactedValue", ""),
        "resource_id": resource_id,
        "resource_group": props.get("resourceMetadata", {}).get("resourceGroup", "") or _rg_from_id(resource_id),
        "subscription_id": settings.AZURE_SUBSCRIPTION_ID,
        "problem_description": short.get("problem", ""),
        "solution_description": short.get("solution", ""),
        "extended_properties": ext,
        "estimated_monthly_savings": monthly,
        "estimated_annual_savings": annual,
        "savings_currency": currency,
        "current_sku": ext.get("currentSku"),
        "recommended_sku": ext.get("targetSku") or ext.get("recommendedSku"),
        "recommendation_type_id": props.get("recommendationTypeId", ""),
        "last_updated": _now(),
        "status": "Active",
        "suppressed": False,
        "suppression_ids": [],
        "sync_timestamp": _now(),
    }


def _rg_from_id(resource_id: str) -> str:
    parts = resource_id.split("/") if resource_id else []
    if "resourceGroups" in parts:
        idx = parts.index("resourceGroups")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


async def sync_advisor_recommendations(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "advisor", None, triggered_by)
    try:
        recs = await advisor.get_all_recommendations(settings.AZURE_SUBSCRIPTION_ID)
        seen_ids: list[str] = []
        for rec in recs:
            mapped = _map_advisor(rec)
            if not mapped["recommendation_id"]:
                continue
            seen_ids.append(mapped["recommendation_id"])
            await db["azure_advisor_recommendations"].update_one(
                {"recommendation_id": mapped["recommendation_id"]},
                {"$set": mapped},
                upsert=True,
            )
        # Mark recommendations that disappeared as Inactive
        await db["azure_advisor_recommendations"].update_many(
            {"recommendation_id": {"$nin": seen_ids}, "status": "Active"},
            {"$set": {"status": "Inactive", "sync_timestamp": _now()}},
        )
        await _write_sync_log_complete(db, log_id, len(seen_ids))
        return len(seen_ids)
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Budgets & alerts
# --------------------------------------------------------------------------- #

def _map_budget(raw: dict) -> dict:
    props = raw.get("properties", {})
    amount = _to_float(props.get("amount"))
    current = _to_float(props.get("currentSpend", {}).get("amount"))
    forecast = props.get("forecastSpend", {}).get("amount")
    forecast_val = _to_float(forecast) if forecast is not None else None
    util = (current / amount * 100) if amount else 0.0
    return {
        "budget_name": raw.get("name", ""),
        "budget_id": raw.get("id", ""),
        "scope": _scope(),
        "category": props.get("category", "Cost"),
        "amount": amount,
        "time_grain": props.get("timeGrain", ""),
        "time_period_start": props.get("timePeriod", {}).get("startDate", ""),
        "time_period_end": props.get("timePeriod", {}).get("endDate", ""),
        "current_spend": current,
        "current_spend_currency": props.get("currentSpend", {}).get("unit", "USD"),
        "forecast_spend": forecast_val,
        "forecast_spend_currency": props.get("forecastSpend", {}).get("unit") if forecast_val is not None else None,
        "utilization_pct": round(util, 2),
        "forecast_utilization_pct": round(forecast_val / amount * 100, 2) if (forecast_val is not None and amount) else None,
        "notifications": list(props.get("notifications", {}).values()) if isinstance(props.get("notifications"), dict) else [],
        "filter": props.get("filter"),
        "sync_timestamp": _now(),
    }


async def sync_budgets(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "budgets", None, triggered_by)
    try:
        budgets = await cost_management.get_budgets(_scope())
        for raw in budgets:
            mapped = _map_budget(raw)
            if not mapped["budget_id"]:
                continue
            await db["azure_budgets"].update_one(
                {"budget_id": mapped["budget_id"]}, {"$set": mapped}, upsert=True
            )
        await _write_sync_log_complete(db, log_id, len(budgets))
        return len(budgets)
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


def _map_alert(raw: dict) -> dict:
    props = raw.get("properties", {})
    definition = props.get("definition", {})
    details = props.get("details", {})
    creation = props.get("creationTime")
    return {
        "alert_id": raw.get("id", ""),
        "alert_name": raw.get("name", ""),
        "alert_type": definition.get("type", ""),
        "category": definition.get("category", ""),
        "criteria": definition.get("criteria", ""),
        "scope": _scope(),
        "description": props.get("description", ""),
        "source": props.get("source", ""),
        "status": props.get("status", ""),
        "creation_time": _parse_dt(creation),
        "close_time": _parse_dt(props.get("closeTime")),
        "budget_name": details.get("budgetName"),
        "budget_id": details.get("budgetId"),
        "threshold": _to_float(details.get("threshold")) if details.get("threshold") is not None else None,
        "current_spend": _to_float(details.get("currentSpend")) if details.get("currentSpend") is not None else None,
        "amount_due": _to_float(details.get("amount")) if details.get("amount") is not None else None,
        "currency": details.get("unit"),
        "triggered_by": details.get("triggeredBy"),
        "time_grain": details.get("timeGrainType"),
        "sync_timestamp": _now(),
    }


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


async def sync_alerts(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "alerts", None, triggered_by)
    try:
        alerts = await cost_management.get_alerts(_scope())
        for raw in alerts:
            mapped = _map_alert(raw)
            if not mapped["alert_id"]:
                continue
            await db["azure_cost_alerts"].update_one(
                {"alert_id": mapped["alert_id"]}, {"$set": mapped}, upsert=True
            )
        await _write_sync_log_complete(db, log_id, len(alerts))
        return len(alerts)
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #

def _map_invoice(raw: dict) -> dict:
    props = raw.get("properties", {})
    period = props.get("invoicePeriodStartDate", "")
    return {
        "invoice_id": raw.get("name", "") or raw.get("id", ""),
        "billing_account_id": settings.AZURE_BILLING_ACCOUNT_ID,
        "billing_profile_id": props.get("billingProfileId"),
        "subscription_id": settings.AZURE_SUBSCRIPTION_ID,
        "billing_period_start": props.get("invoicePeriodStartDate", ""),
        "billing_period_end": props.get("invoicePeriodEndDate", ""),
        "invoice_date": props.get("invoiceDate", "") or period,
        "due_date": props.get("dueDate"),
        "billed_amount": _to_float(props.get("totalAmount", {}).get("value") if isinstance(props.get("totalAmount"), dict) else props.get("billedAmount")),
        "amount_due": _to_float(props.get("amountDue", {}).get("value") if isinstance(props.get("amountDue"), dict) else props.get("amountDue")),
        "azure_prepayment_applied": _to_float(props.get("azurePrepaymentApplied", {}).get("value") if isinstance(props.get("azurePrepaymentApplied"), dict) else 0),
        "credit_amount": _to_float(props.get("creditAmount", {}).get("value") if isinstance(props.get("creditAmount"), dict) else 0),
        "tax_amount": _to_float(props.get("taxAmount", {}).get("value") if isinstance(props.get("taxAmount"), dict) else 0),
        "billing_currency": props.get("billingCurrency", "USD"),
        "status": props.get("status", ""),
        "purchase_order_number": props.get("purchaseOrderNumber"),
        "invoice_download_url": None,
        "invoice_download_expiry": None,
        "sync_timestamp": _now(),
    }


async def sync_invoices(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "invoices", None, triggered_by)
    try:
        invoices = await billing_accounts.get_invoices(
            settings.AZURE_BILLING_ACCOUNT_ID, settings.AZURE_BILLING_ACCOUNT_TYPE
        )
        for raw in invoices:
            mapped = _map_invoice(raw)
            if not mapped["invoice_id"]:
                continue
            await db["azure_invoices"].update_one(
                {"invoice_id": mapped["invoice_id"]}, {"$set": mapped}, upsert=True
            )
        await _write_sync_log_complete(db, log_id, len(invoices))
        return len(invoices)
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Reservations
# --------------------------------------------------------------------------- #

def _map_reservation_detail(raw: dict) -> dict:
    props = raw.get("properties", raw)
    reserved = _to_float(props.get("reservedHours"))
    utilized = _to_float(props.get("usedHours") or props.get("utilizedHours"))
    usage_date = str(props.get("usageDate", ""))[:10]
    billing_period = usage_date[:7] if usage_date else ""
    return {
        "reservation_id": props.get("reservationId", ""),
        "reservation_order_id": props.get("reservationOrderId", ""),
        "sku_name": props.get("skuName", ""),
        "kind": props.get("kind", ""),
        "instance_flexibility_group": props.get("instanceFlexibilityGroup"),
        "instance_flexibility_ratio": _to_float(props.get("instanceFlexibilityRatio")) if props.get("instanceFlexibilityRatio") is not None else None,
        "usage_date": usage_date,
        "billing_period": billing_period,
        "utilized_hours": utilized,
        "reserved_hours": reserved,
        "total_reserved_quantity": _to_float(props.get("totalReservedQuantity")),
        "utilization_pct": round(utilized / reserved * 100, 2) if reserved else 0.0,
        "sync_timestamp": _now(),
    }


def _map_reservation_recommendation(raw: dict) -> dict:
    props = raw.get("properties", raw)
    return {
        "subscription_id": settings.AZURE_SUBSCRIPTION_ID,
        "sku_name": props.get("skuName", "") or props.get("displaySkuName", ""),
        "resource_type": props.get("resourceType", ""),
        "scope": props.get("scope", "Single"),
        "term": props.get("term", ""),
        "look_back_period": props.get("lookBackPeriod", ""),
        "location": props.get("location", "") or raw.get("location", ""),
        "recommended_quantity": _to_float(props.get("recommendedQuantity")),
        "recommended_quantity_normalized": _to_float(props.get("recommendedQuantityNormalized")),
        "instance_flexibility_group": props.get("instanceFlexibilityGroup"),
        "instance_flexibility_ratio": _to_float(props.get("instanceFlexibilityRatio")) if props.get("instanceFlexibilityRatio") is not None else None,
        "meter_id": props.get("meterId"),
        "first_usage_date": props.get("firstUsageDate"),
        "total_cost_with_no_ri": _to_float(props.get("costWithNoReservedInstances")),
        "total_cost_with_ri": _to_float(props.get("totalCostWithReservedInstances")),
        "net_savings": _to_float(props.get("netSavings")),
        "currency": props.get("currencyCode", "USD"),
        "sku_properties": props.get("skuProperties", []),
        "sync_timestamp": _now(),
    }


async def sync_reservations(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "reservations", None, triggered_by)
    try:
        scope = _scope()
        count = 0
        now = _now()
        start, end = _period_dates(f"{now.year:04d}-{now.month:02d}")
        details = await consumption.get_reservation_details(scope, start, end)
        for raw in details:
            mapped = _map_reservation_detail(raw)
            if not mapped["reservation_id"] or not mapped["usage_date"]:
                continue
            await db["azure_reservation_details"].update_one(
                {"reservation_id": mapped["reservation_id"], "usage_date": mapped["usage_date"]},
                {"$set": mapped},
                upsert=True,
            )
            count += 1

        for term in ("P1Y", "P3Y"):
            for look_back in ("Last30Days", "Last60Days"):
                recs = await consumption.get_reservation_recommendations(scope, term, look_back)
                for raw in recs:
                    mapped = _map_reservation_recommendation(raw)
                    key = {
                        "subscription_id": mapped["subscription_id"],
                        "sku_name": mapped["sku_name"],
                        "term": mapped["term"],
                        "scope": mapped["scope"],
                        "location": mapped["location"],
                    }
                    await db["azure_reservation_recommendations"].update_one(key, {"$set": mapped}, upsert=True)
                    count += 1
        await _write_sync_log_complete(db, log_id, count)
        return count
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Resource inventory
# --------------------------------------------------------------------------- #

def _map_resource(raw: dict) -> dict:
    return {
        "resource_id": raw.get("id", ""),
        "resource_name": raw.get("name", ""),
        "resource_type": raw.get("type", ""),
        "subscription_id": raw.get("subscriptionId", "") or settings.AZURE_SUBSCRIPTION_ID,
        "resource_group": raw.get("resourceGroup", ""),
        "location": raw.get("location", ""),
        "sku": raw.get("sku"),
        "kind": raw.get("kind"),
        "tags": raw.get("tags") or {},
        "provisioning_state": (raw.get("properties") or {}).get("provisioningState") if isinstance(raw.get("properties"), dict) else None,
        "power_state": raw.get("powerState"),
        "properties": {},
        "sync_timestamp": _now(),
    }


async def sync_resource_inventory(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "resource_inventory", None, triggered_by)
    try:
        subs = [settings.AZURE_SUBSCRIPTION_ID]
        resources = await resource_graph.query_resources(subs, resource_graph.KQL_ALL_RESOURCES)
        deallocated = await resource_graph.query_resources(subs, resource_graph.KQL_DEALLOCATED_VMS)
        power_map = {d.get("id"): d.get("powerState") for d in deallocated}

        for raw in resources:
            mapped = _map_resource(raw)
            if not mapped["resource_id"]:
                continue
            if mapped["resource_id"] in power_map:
                mapped["power_state"] = power_map[mapped["resource_id"]]
            await db["azure_resource_inventory"].update_one(
                {"resource_id": mapped["resource_id"]}, {"$set": mapped}, upsert=True
            )
        await _write_sync_log_complete(db, log_id, len(resources))
        return len(resources)
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Retail prices
# --------------------------------------------------------------------------- #

def _map_retail_price(raw: dict) -> dict:
    return {
        "meter_id": raw.get("meterId", ""),
        "sku_id": raw.get("skuId", ""),
        "product_id": raw.get("productId", ""),
        "meter_name": raw.get("meterName", ""),
        "product_name": raw.get("productName", ""),
        "sku_name": raw.get("skuName", ""),
        "service_name": raw.get("serviceName", ""),
        "service_family": raw.get("serviceFamily", ""),
        "service_id": raw.get("serviceId", ""),
        "arm_region_name": raw.get("armRegionName", ""),
        "location": raw.get("location", ""),
        "retail_price": _to_float(raw.get("retailPrice")),
        "unit_price": _to_float(raw.get("unitPrice")),
        "currency_code": raw.get("currencyCode", "USD"),
        "unit_of_measure": raw.get("unitOfMeasure", ""),
        "type": raw.get("type", ""),
        "tier_minimum_units": _to_float(raw.get("tierMinimumUnits")),
        "is_primary_meter_region": bool(raw.get("isPrimaryMeterRegion")),
        "arm_sku_name": raw.get("armSkuName"),
        "effective_start_date": raw.get("effectiveStartDate", ""),
        "effective_end_date": raw.get("effectiveEndDate"),
        "sync_timestamp": _now(),
    }


async def sync_retail_prices(db, triggered_by: str = "manual_api") -> int:
    log_id = await _write_sync_log_start(db, "retail_prices", None, triggered_by)
    try:
        prices = await retail_prices.sync_common_service_prices()
        for raw in prices:
            mapped = _map_retail_price(raw)
            if not mapped["meter_id"]:
                continue
            key = {
                "meter_id": mapped["meter_id"],
                "arm_region_name": mapped["arm_region_name"],
                "type": mapped["type"],
            }
            await db["azure_retail_prices"].update_one(key, {"$set": mapped}, upsert=True)
        await _write_sync_log_complete(db, log_id, len(prices))
        return len(prices)
    except Exception as exc:  # noqa: BLE001
        await _write_sync_log_failed(db, log_id, str(exc))
        raise


# --------------------------------------------------------------------------- #
# Composite syncs
# --------------------------------------------------------------------------- #

async def run_daily_sync(db) -> dict:
    """Daily: current + previous month cost details, plus budgets and alerts."""
    now = _now()
    current = f"{now.year:04d}-{now.month:02d}"
    prev_month = now.month - 1 or 12
    prev_year = now.year if now.month > 1 else now.year - 1
    previous = f"{prev_year:04d}-{prev_month:02d}"

    summary = {
        "cost_details_current": await sync_cost_details(db, current, "scheduler"),
        "cost_details_previous": await sync_cost_details(db, previous, "scheduler"),
        "budgets": await sync_budgets(db, "scheduler"),
        "alerts": await sync_alerts(db, "scheduler"),
    }
    return summary


async def run_full_backfill(db, months: int, triggered_by: str = "startup_backfill") -> dict:
    """One-time historical backfill across all sync types. No-op if data exists."""
    existing = await db["azure_cost_details"].count_documents({})
    if existing:
        logger.info("Cost details already populated (%d docs). Skipping backfill.", existing)
        return {"skipped": True}

    summary: dict = {"cost_details": 0}
    for period in _recent_periods(months):
        summary["cost_details"] += await sync_cost_details(db, period, triggered_by)
        await asyncio.sleep(5)  # Respect rate limits between periods

    summary["advisor"] = await sync_advisor_recommendations(db, triggered_by)
    summary["budgets"] = await sync_budgets(db, triggered_by)
    summary["invoices"] = await sync_invoices(db, triggered_by)
    summary["reservations"] = await sync_reservations(db, triggered_by)
    summary["resource_inventory"] = await sync_resource_inventory(db, triggered_by)
    summary["retail_prices"] = await sync_retail_prices(db, triggered_by)
    return summary
