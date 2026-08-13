# Task: Vectorizer Hardening + Sync Data Validation — RecoveryHub Dashboard

You are working on the RecoveryHub Dashboard project at `E:\gitrepo\RH Dashboard`.
This is a FastAPI backend that includes a billing data sync pipeline and an AI
vectorization pipeline. The vectorizer reads billing data from MongoDB, generates
natural-language documents, calls OpenAI (or Azure OpenAI) for embeddings, and
upserts the results into a vector search collection.

Your job is to harden two areas identified in a production readiness review:

1. **Vectorizer hardening (S5):** rate limiting, quota/429 handling, empty-text
   validation, and cost controls for the OpenAI embedding pipeline.
2. **Sync data validation (S6):** validate upstream Azure API responses before
   writing to MongoDB, so malformed data doesn't silently corrupt collections.

Implement every fix listed below. Do NOT skip any. After all fixes are
implemented, run the test suites to verify nothing broke.

## Project context (read these files first to understand the codebase)

- `AGENTS.md` — project notes, build/test commands
- `backend/config.py` — settings (pydantic-free Settings class, env vars)
- `backend/billing/__init__.py` — custom exceptions (BillingAPIError, BillingConfigError, VectorizerError)
- `backend/billing/vectorizer.py` — the vectorization pipeline (embed_documents, run_vectorization, etc.)
- `backend/billing/sync_service.py` — sync orchestration with _clean_cost_row and other _map_* functions
- `backend/billing/cost_management.py` — Azure API client with _api_call_with_retry (reference for retry pattern)
- `backend/tests/test_billing_vectorizer.py` — existing vectorizer tests
- `backend/tests/test_billing_sync_service.py` — existing sync service tests
- `backend/tests/conftest.py` — test fixtures (mock_mongo_db, mock_openai pattern)

## Build & test commands

```powershell
# Backend tests (from backend/)
$env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v
```

---

## FIX S5: Vectorizer hardening

**File:** `backend/billing/vectorizer.py`

### S5a: Add configurable rate-limiting and cost-control settings to config.py

**File:** `backend/config.py`

Add these settings to the `Settings` class (in the "AI / Embeddings" section,
after the existing `AZURE_OPENAI_API_VERSION` line ~71):

```python
    # --- Vectorizer rate limiting and cost controls ---
    # Max documents to embed in a single run_vectorization call. Prevents
    # runaway costs on large billing periods. 0 = no limit.
    VECTORIZER_MAX_DOCUMENTS: int = int(os.getenv("VECTORIZER_MAX_DOCUMENTS", "5000"))
    # Batch size for embedding API calls. OpenAI supports up to 2048 inputs
    # per request, but 100 is conservative for token-limit safety.
    VECTORIZER_BATCH_SIZE: int = int(os.getenv("VECTORIZER_BATCH_SIZE", "100"))
    # Seconds to sleep between batches (rate limiting). Set to 0 to disable.
    VECTORIZER_BATCH_DELAY: float = float(os.getenv("VECTORIZER_BATCH_DELAY", "0.5"))
    # Max retries on transient embedding API errors (429, 500, 503, timeout).
    VECTORIZER_MAX_RETRIES: int = int(os.getenv("VECTORIZER_MAX_RETRIES", "3"))
    # Minimum text length (chars) to warrant embedding. Shorter texts produce
    # low-quality vectors and waste API quota.
    VECTORIZER_MIN_TEXT_LENGTH: int = int(os.getenv("VECTORIZER_MIN_TEXT_LENGTH", "10"))
```

Also add validation for these in `validate_settings()` (after the JWKS
validation block, around line ~135):

```python
        # Validate vectorizer parameters
        if self.VECTORIZER_MAX_DOCUMENTS < 0:
            missing.append("VECTORIZER_MAX_DOCUMENTS (must be >= 0)")
        if self.VECTORIZER_BATCH_SIZE < 1:
            missing.append("VECTORIZER_BATCH_SIZE (must be >= 1)")
        if self.VECTORIZER_BATCH_DELAY < 0:
            missing.append("VECTORIZER_BATCH_DELAY (must be >= 0)")
        if self.VECTORIZER_MAX_RETRIES < 0:
            missing.append("VECTORIZER_MAX_RETRIES (must be >= 0)")
        if self.VECTORIZER_MIN_TEXT_LENGTH < 0:
            missing.append("VECTORIZER_MIN_TEXT_LENGTH (must be >= 0)")
```

### S5b: Filter empty/short text before embedding

**File:** `backend/billing/vectorizer.py`, function `embed_documents` (line ~287)

**Current code:**
```python
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
```

**Problem:** No validation that document text is non-empty. Empty strings or
None values are sent to OpenAI, wasting API calls and producing poor vectors.
No rate limiting, no retry on transient failures, no quota handling, no max
document cap.

**Replace with:**
```python
async def embed_documents(documents: list[dict]) -> list[dict]:
    """Generates embeddings for documents in configurable batches.

    Filters out documents with empty/short text, enforces a max document count,
    retries transient API failures (429/500/503/timeout) with exponential
    backoff, and sleeps between batches to respect rate limits.
    """
    client = _get_openai_client()
    min_len = settings.VECTORIZER_MIN_TEXT_LENGTH
    max_docs = settings.VECTORIZER_MAX_DOCUMENTS
    batch_size = settings.VECTORIZER_BATCH_SIZE
    batch_delay = settings.VECTORIZER_BATCH_DELAY
    max_retries = settings.VECTORIZER_MAX_RETRIES

    # Filter out documents with missing/empty/short text
    valid_docs = [
        doc for doc in documents
        if doc.get("text") and len(doc["text"].strip()) >= min_len
    ]
    skipped = len(documents) - len(valid_docs)
    if skipped > 0:
        logger.warning(f"Filtered {skipped} documents with empty or short text (< {min_len} chars).")

    # Enforce max document cap
    if max_docs > 0 and len(valid_docs) > max_docs:
        logger.warning(
            f"Document count {len(valid_docs)} exceeds VECTORIZER_MAX_DOCUMENTS "
            f"({max_docs}). Truncating to {max_docs}."
        )
        valid_docs = valid_docs[:max_docs]

    if not valid_docs:
        logger.info("No valid documents to embed after filtering.")
        return documents  # Return original list (unchanged, no embeddings added)

    for i in range(0, len(valid_docs), batch_size):
        batch = valid_docs[i:i + batch_size]
        texts = [doc["text"] for doc in batch]
        batch_num = i // batch_size

        # Retry transient failures with exponential backoff
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.embeddings.create(
                    model=settings.OPENAI_EMBEDDING_MODEL,
                    input=texts,
                )
                for j, embedding_data in enumerate(response.data):
                    batch[j]["embedding"] = embedding_data.embedding
                last_error = None
                break  # Success, exit retry loop
            except Exception as e:  # noqa: BLE001
                last_error = e
                error_str = str(e).lower()
                # Check for transient/retryable errors
                is_transient = (
                    "429" in error_str
                    or "rate limit" in error_str
                    or "timeout" in error_str
                    or "timed out" in error_str
                    or "500" in error_str
                    or "503" in error_str
                    or "service unavailable" in error_str
                    or "internal server error" in error_str
                )
                # Check for quota-exhausted (non-retryable)
                is_quota_exhausted = (
                    "quota" in error_str
                    or "insufficient_quota" in error_str
                    or "billing" in error_str
                )

                if is_quota_exhausted:
                    logger.error(
                        f"OpenAI quota exhausted at batch {batch_num}. "
                        f"Aborting vectorization. Error: {e}"
                    )
                    raise VectorizerError(
                        f"OpenAI quota exhausted at batch {batch_num}: {e}. "
                        f"{len(valid_docs) - i} documents will not be vectorized."
                    )

                if not is_transient or attempt == max_retries:
                    logger.error(
                        f"Embedding batch {batch_num} failed after {attempt} attempts: {e}"
                    )
                    raise VectorizerError(
                        f"Embedding generation failed for batch {batch_num}: {e}"
                    )

                # Exponential backoff: 2^attempt seconds, capped at 60
                wait = min(2 ** attempt, 60)
                logger.warning(
                    f"Embedding batch {batch_num} attempt {attempt}/{max_retries} "
                    f"failed (transient). Retrying in {wait}s. Error: {e}"
                )
                await asyncio.sleep(wait)

        if last_error is not None:
            # Should not reach here (loop either breaks on success or raises),
            # but guard against logic errors.
            raise VectorizerError(f"Embedding generation failed for batch {batch_num}: {last_error}")

        if i + batch_size < len(valid_docs) and batch_delay > 0:
            await asyncio.sleep(batch_delay)

    return documents
```

**Important notes on this replacement:**
- The function still returns the original `documents` list (not `valid_docs`).
  Documents that were filtered out or truncated won't have an `"embedding"`
  key added. The caller (`upsert_vectors`) checks `doc.get("embedding", [])`
  for the dimensions field, so un-embedded docs will have `dimensions: 0`.
  However, `upsert_vectors` upserts ALL docs in the list. To avoid upserting
  un-embedded docs, we need to also filter in `run_vectorization` (see S5c).
- The existing test `test_embed_documents_batching` creates 250 docs with
  text `"doc {i}"` (all > 10 chars) and expects 3 API calls. With the new
  batch_size from settings (default 100), this should still produce 3 batches.
  BUT the test mocks `_get_openai_client` and doesn't set the env vars for
  batch_size. Since config.py reads env vars at import time and conftest.py
  sets `TESTING=true` before imports, the defaults (100, 0.5, 3 retries, 10
  min chars) will be used. Verify the test still passes — 250 docs / 100 per
  batch = 3 batches. The test also checks `all(d["embedding"])` — all 250
  docs have valid text so all get embedded. This should work.
- The existing test `test_embed_documents_sleeps_between_batches` expects
  1 sleep call for 150 docs (2 batches). With the new code, the sleep is
  `await asyncio.sleep(batch_delay)` which only fires if `batch_delay > 0`.
  The default is 0.5, so this should still work. The test mocks
  `vectorizer.asyncio.sleep` — verify the mock still catches the call.

### S5c: Filter un-embedded documents before upsert in run_vectorization

**File:** `backend/billing/vectorizer.py`, function `run_vectorization` (line ~336)

**Current code:**
```python
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
```

**Problem:** `embed_documents` now filters out short-text docs but returns
the original list. `upsert_vectors` would upsert ALL docs including the
filtered ones (with no embedding). Need to filter to only docs that actually
got embeddings.

**Replace with:**
```python
async def run_vectorization(db) -> int:
    """Generates and upserts embeddings for the current and prior billing periods.

    Only documents that received embeddings are upserted; filtered/short-text
    documents are skipped.
    """
    today = date.today()
    current_period = today.strftime("%Y-%m")
    prior_month = today.replace(day=1) - timedelta(days=1)
    prior_period = prior_month.strftime("%Y-%m")

    total = 0
    for period in [prior_period, current_period]:
        docs = await generate_billing_documents(db, period)
        if not docs:
            continue
        await embed_documents(docs)
        # Only upsert documents that actually received embeddings
        docs_with_embeddings = [d for d in docs if "embedding" in d and d["embedding"]]
        if not docs_with_embeddings:
            logger.info(f"No documents with embeddings to upsert for period {period}")
            continue
        count = await upsert_vectors(db, docs_with_embeddings)
        total += count
        logger.info(f"Vectorized {count} documents for period {period}")
    return total
```

### S5d: Add tests for vectorizer hardening

**File:** `backend/tests/test_billing_vectorizer.py` (append to existing file)

Add these tests:

```python
@pytest.mark.asyncio
async def test_embed_documents_filters_short_text(mock_openai, mock_mongo_db):
    """Documents with text shorter than VECTORIZER_MIN_TEXT_LENGTH are skipped."""
    # Note: conftest sets TESTING=true before config import, so defaults apply.
    # Default VECTORIZER_MIN_TEXT_LENGTH is 10.
    docs = [
        {"text": "This is a valid document with enough text", "metadata": {}},
        {"text": "short", "metadata": {}},  # 5 chars, below minimum
        {"text": "", "metadata": {}},        # empty
        {"text": "   ", "metadata": {}},     # whitespace only
    ]
    await vectorizer.embed_documents(docs)
    # Only the first doc should have an embedding
    assert "embedding" in docs[0]
    assert "embedding" not in docs[1]
    assert "embedding" not in docs[2]
    assert "embedding" not in docs[3]


@pytest.mark.asyncio
async def test_embed_documents_retries_on_transient_error(monkeypatch, mock_openai):
    """Embedding retries on 429 and succeeds on second attempt."""
    call_count = 0

    async def _create_embeddings(model, input):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("429 Rate limit exceeded")
        data = [MagicMock(embedding=[0.0] * 1536) for _ in input]
        resp = MagicMock()
        resp.data = data
        return resp

    mock_openai.embeddings.create = AsyncMock(side_effect=_create_embeddings)
    # Speed up the test: don't actually sleep
    monkeypatch.setattr(vectorizer.asyncio, "sleep", AsyncMock())

    docs = [{"text": "This is a valid document for embedding test", "metadata": {}}]
    result = await vectorizer.embed_documents(docs)
    assert call_count == 2  # First failed, second succeeded
    assert "embedding" in result[0]


@pytest.mark.asyncio
async def test_embed_documents_raises_on_quota_exhausted(monkeypatch, mock_openai):
    """Quota-exhausted errors are not retried — raise immediately."""
    async def _quota_error(model, input):
        raise Exception("insufficient_quota: You exceeded your quota")

    mock_openai.embeddings.create = AsyncMock(side_effect=_quota_error)
    monkeypatch.setattr(vectorizer.asyncio, "sleep", AsyncMock())

    from billing import VectorizerError
    docs = [{"text": "This is a valid document for embedding test", "metadata": {}}]
    with pytest.raises(VectorizerError, match="quota exhausted"):
        await vectorizer.embed_documents(docs)


@pytest.mark.asyncio
async def test_embed_documents_raises_after_max_retries(monkeypatch, mock_openai):
    """Transient errors are retried up to VECTORIZER_MAX_RETRIES, then raised."""
    async def _persistent_503(model, input):
        raise Exception("503 Service unavailable")

    mock_openai.embeddings.create = AsyncMock(side_effect=_persistent_503)
    monkeypatch.setattr(vectorizer.asyncio, "sleep", AsyncMock())

    from billing import VectorizerError
    docs = [{"text": "This is a valid document for embedding test", "metadata": {}}]
    with pytest.raises(VectorizerError, match="failed for batch 0"):
        await vectorizer.embed_documents(docs)
    # Default max retries is 3
    assert mock_openai.embeddings.create.await_count == 3


@pytest.mark.asyncio
async def test_run_vectorization_skips_unembedded_docs(mock_openai, mock_mongo_db):
    """run_vectorization only upserts documents that received embeddings."""
    # Insert some cost summary data so documents are generated
    await mock_mongo_db["azure_cost_summary"].insert_many([
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": "VM", "total_cost": 4000.0, "currency": "USD", "usage_quantity": 10, "unit_of_measure": "Hours"},
    ])
    # Mock date.today to return a fixed date in the test period
    import datetime as dt
    original_today = dt.date.today

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 5, 15)

    monkeypatch.setattr(dt, "date", FakeDate)
    try:
        count = await vectorizer.run_vectorization(mock_mongo_db)
        # Should have upserted at least 1 doc (top_spenders + service_cost_detail)
        assert count >= 1
        # All upserted docs should have embeddings
        vectors = await mock_mongo_db["azure_billing_vectors"].find({}).to_list(length=100)
        for v in vectors:
            assert "embedding" in v
            assert len(v["embedding"]) > 0
    finally:
        monkeypatch.setattr(dt, "date", original_today)
```

---

## FIX S6: Sync data validation before MongoDB writes

**File:** `backend/billing/sync_service.py`

### S6a: Validate cost detail rows before upsert

**Current code (line ~160):**
```python
def _clean_cost_row(row: dict, billing_period: str) -> dict:
    """Maps a raw CSV cost-detail row to the azure_cost_details schema."""
    resource_id = row.get("ResourceId", "") or ""
    date_raw = row.get("Date", "") or ""
    return {
        "billing_period": billing_period,
        "date": date_raw,
        ...
    }
```

**Problem:** No validation. If Azure returns a row missing `ResourceId`,
`Date`, or `MeterId`, the row gets upserted with empty strings for critical
fields. This silently corrupts MongoDB — aggregations group by empty strings,
queries can't find records, and the data is unusable.

**Fix:** Add validation to `_clean_cost_row` that returns `None` for rows
missing critical fields. Update the caller to skip `None` results.

Replace `_clean_cost_row` with:
```python
def _clean_cost_row(row: dict, billing_period: str) -> dict | None:
    """Maps a raw CSV cost-detail row to the azure_cost_details schema.

    Returns None if the row is missing critical fields (Date, MeterId),
    indicating it should be skipped rather than upserted with empty values.
    ResourceId is allowed to be empty (some charge types don't have one).
    """
    date_raw = row.get("Date", "") or ""
    meter_id = row.get("MeterId", "") or ""

    # Critical fields — skip rows missing these to prevent silent data corruption
    if not date_raw:
        logger.warning("Skipping cost row: missing Date field.")
        return None
    if not meter_id:
        logger.warning("Skipping cost row: missing MeterId field.")
        return None

    resource_id = row.get("ResourceId", "") or ""
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
        "meter_id": meter_id,
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
        "is_zure_credit_eligible": _to_bool(row.get("IsAzureCreditEligible")),
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
```

**IMPORTANT:** Copy the field list EXACTLY from the existing code. Do NOT
retype or reorder fields — only change the function signature (add `| None`
return type), add the validation block at the top, and change `meter_id` to
use the validated local variable. Read the existing function first and
preserve every field exactly.

### S6b: Update the caller to skip None results

**File:** `backend/billing/sync_service.py`, function `sync_cost_details` (line ~280)

**Current code (within the sync function, inside the lock):**
```python
        for metric in ("ActualCost", "AmortizedCost"):
            rows = await cost_management.generate_cost_details_report(scope, start, end, metric)
            for row in rows:
                await _upsert_cost_row(db, _clean_cost_row(row, billing_period))
                count += 1
```

**Replace with:**
```python
        for metric in ("ActualCost", "AmortizedCost"):
            rows = await cost_management.generate_cost_details_report(scope, start, end, metric)
            for row in rows:
                cleaned = _clean_cost_row(row, billing_period)
                if cleaned is None:
                    continue
                await _upsert_cost_row(db, cleaned)
                count += 1
```

### S6c: Add validation to advisor recommendation mapping

**File:** `backend/billing/sync_service.py`, function `_map_advisor` (line ~309)

The existing code already skips records with no `recommendation_id` in the
sync function (line ~318: `if not mapped["recommendation_id"]: continue`).
This is good. Add a similar guard for `category` — if the category is empty,
the recommendation is malformed and shouldn't be stored.

Add this check at the top of `_map_advisor`:
```python
def _map_advisor(rec: dict) -> dict | None:
    """Maps a raw Advisor recommendation to the azure_advisor_recommendations schema.

    Returns None if the record is missing critical fields (recommendation_id, category).
    """
    rec_id = rec.get("name", "")
    props = rec.get("properties", {})
    category = props.get("category", "")

    if not rec_id:
        return None
    if not category:
        logger.warning(f"Skipping advisor recommendation: missing category (id={rec_id}).")
        return None

    ext = props.get("extendedProperty", {})
    # ... rest of existing mapping logic unchanged ...
```

**IMPORTANT:** Read the existing `_map_advisor` function first. Only add the
validation block at the top and change the return type to `dict | None`.
The rest of the mapping logic (extracting savings, resource_id, etc.) stays
exactly the same. Make sure the existing `props` variable assignment is not
duplicated — merge it with the new validation code.

### S6d: Update the advisor sync caller to skip None results

**File:** `backend/billing/sync_service.py`, function `sync_advisor_recommendations` (line ~347)

**Current code (inside the lock):**
```python
        for rec in recs:
            mapped = _map_advisor(rec)
            if not mapped["recommendation_id"]:
                continue
            seen_ids.append(mapped["recommendation_id"])
            ...
```

**Replace with:**
```python
        for rec in recs:
            mapped = _map_advisor(rec)
            if mapped is None:
                continue
            if not mapped["recommendation_id"]:
                continue
            seen_ids.append(mapped["recommendation_id"])
            ...
```

(The second `if not mapped["recommendation_id"]` check is now redundant
since `_map_advisor` already checks for it, but keep it as a defensive
double-check — it's harmless.)

### S6e: Add tests for data validation

**File:** `backend/tests/test_billing_sync_service.py` (append to existing file)

Add these tests:

```python
def test_clean_cost_row_returns_none_for_missing_date():
    """Rows missing the Date field are rejected (return None)."""
    row = _csv_row()
    row["Date"] = ""
    result = sync_service._clean_cost_row(row, "2026-05")
    assert result is None


def test_clean_cost_row_returns_none_for_missing_meter_id():
    """Rows missing the MeterId field are rejected (return None)."""
    row = _csv_row()
    row["MeterId"] = ""
    result = sync_service._clean_cost_row(row, "2026-05")
    assert result is None


def test_clean_cost_row_allows_empty_resource_id():
    """ResourceId is optional (some charge types don't have one) — should not reject."""
    row = _csv_row()
    row["ResourceId"] = ""
    result = sync_service._clean_cost_row(row, "2026-05")
    assert result is not None
    assert result["resource_id"] == ""


def test_map_advisor_returns_none_for_missing_category():
    """Advisor records missing category are rejected (return None)."""
    rec = {
        "name": "rec-1",
        "id": "/arm/rec-1",
        "properties": {
            "category": "",  # Missing
            "impact": "High",
            "impactedField": "Microsoft.Compute/virtualMachines",
            "impactedValue": "vm1",
            "shortDescription": {"problem": "Idle VM", "solution": "Resize"},
            "extendedProperties": {"savingsAmount": "100", "savingsCurrency": "USD"},
            "resourceMetadata": {"resourceId": "/subscriptions/sub1/resourceGroups/rg1/x"},
        },
    }
    result = sync_service._map_advisor(rec)
    assert result is None


def test_map_advisor_returns_none_for_missing_id():
    """Advisor records missing recommendation_id (name) are rejected."""
    rec = {
        "name": "",
        "id": "/arm/rec-1",
        "properties": {
            "category": "Cost",
            "impact": "High",
        },
    }
    result = sync_service._map_advisor(rec)
    assert result is None


@pytest.mark.asyncio
async def test_sync_cost_details_skips_invalid_rows(monkeypatch, mock_mongo_db):
    """Invalid rows (missing Date/MeterId) are skipped during sync, valid ones are upserted."""
    async def fake_report(scope, start, end, metric="ActualCost"):
        return [
            _csv_row(),                              # valid
            _csv_row(MeterId="meter2", Cost="5.00"),  # valid
            _csv_row(Date=""),                        # invalid (no date)
            _csv_row(MeterId=""),                     # invalid (no meter_id)
        ]
    monkeypatch.setattr(sync_service.cost_management, "generate_cost_details_report", fake_report)

    count = await sync_service.sync_cost_details(mock_mongo_db, "2026-05", "manual_api")
    # 2 valid rows x 2 metrics = 4 upserts (invalid rows skipped)
    assert count == 4
```

---

## After all fixes: verify

1. Run backend tests:
   ```powershell
   cd backend
   $env:TESTING="true"; .venv\Scripts\python.exe -m pytest -v
   ```
   All existing tests must still pass, plus the new tests. Pay special
   attention to:
   - `test_billing_vectorizer.py` — the existing batching/sleep tests must
     still pass with the new configurable settings (defaults match old behavior)
   - `test_billing_sync_service.py` — the existing `test_clean_cost_row_maps_fields`
     test must still pass (it uses `_csv_row()` which has all required fields)
   - `test_config_validation.py` — if it validates config, the new
     VECTORIZER_* settings must not break it

   If any test fails, fix the issue and re-run until all pass.

2. Check that no `to_list(length=None)` was reintroduced:
   ```powershell
   cd backend
   Select-String -Path "*.py","billing\*.py","ai_analytics\*.py" -Pattern "to_list\(length=None\)" -Recurse
   ```
   Only test files should have hits.

## Constraints

- Do NOT change any function signatures except where explicitly stated
  (`_clean_cost_row` and `_map_advisor` get `| None` added to return type).
- Do NOT change any logic inside the sync function bodies except the
  specific lines called out in S6b and S6d.
- Do NOT remove existing comments unless directly replacing them.
- Do NOT add emojis to code or comments.
- Follow existing code style in each file (indentation, quoting, etc.).
- When copying field lists (e.g., in `_clean_cost_row`), read the existing
  code first and preserve every field exactly — do not retype from memory.
- Leave the changes as unstaged working changes. Do NOT push, commit, or
  stage any changes. Do NOT run `git add`, `git commit`, or `git push`.
