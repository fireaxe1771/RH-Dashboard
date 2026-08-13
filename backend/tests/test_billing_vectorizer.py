"""Tests for billing.vectorizer — OpenAI client and Atlas search mocked."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from billing import vectorizer


@pytest.fixture
def mock_openai(monkeypatch):
    client = MagicMock()
    # embeddings.create returns 1536-dim vectors, one per input text
    async def _create_embeddings(model, input):
        data = [MagicMock(embedding=[0.0] * 1536) for _ in input]
        resp = MagicMock()
        resp.data = data
        return resp
    client.embeddings.create = AsyncMock(side_effect=_create_embeddings)
    monkeypatch.setattr(vectorizer, "_get_openai_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_generate_billing_documents_empty_db(mock_mongo_db):
    docs = await vectorizer.generate_billing_documents(mock_mongo_db, "2026-05")
    assert docs == []


@pytest.mark.asyncio
async def test_generate_top_spenders_document(mock_mongo_db):
    await mock_mongo_db["azure_cost_summary"].insert_many([
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": "Virtual Machines", "total_cost": 4000.0, "currency": "USD", "usage_quantity": 10, "unit_of_measure": "Hours"},
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": "Storage", "total_cost": 1000.0, "currency": "USD", "usage_quantity": 5, "unit_of_measure": "GB"},
    ])
    docs = await vectorizer.generate_billing_documents(mock_mongo_db, "2026-05")
    types = {d["document_type"] for d in docs}
    assert "top_spenders" in types
    assert "service_cost_detail" in types
    top = next(d for d in docs if d["document_type"] == "top_spenders")
    assert "Virtual Machines" in top["text"]
    assert top["metadata"]["total_cost"] == 5000.0


@pytest.mark.asyncio
async def test_embed_documents_batching(mock_openai):
    docs = [{"text": f"document number {i}", "metadata": {}} for i in range(250)]
    result = await vectorizer.embed_documents(docs)
    # 250 docs -> ceil(250/100) = 3 batches
    assert mock_openai.embeddings.create.await_count == 3
    assert all(len(d["embedding"]) == 1536 for d in result)


@pytest.mark.asyncio
async def test_embed_documents_sleeps_between_batches(monkeypatch, mock_openai):
    sleep_mock = AsyncMock()
    monkeypatch.setattr(vectorizer.asyncio, "sleep", sleep_mock)
    docs = [{"text": f"document number {i}", "metadata": {}} for i in range(150)]
    await vectorizer.embed_documents(docs)
    # 2 batches -> 1 inter-batch sleep
    assert sleep_mock.await_count == 1


@pytest.mark.asyncio
async def test_upsert_vectors_idempotent(mock_mongo_db):
    docs = [{
        "document_type": "top_spenders",
        "text": "x",
        "embedding": [0.0] * 1536,
        "metadata": {"period": "2026-05", "dimension_value": "all_services"},
    }]
    await vectorizer.upsert_vectors(mock_mongo_db, docs)
    await vectorizer.upsert_vectors(mock_mongo_db, docs)
    count = await mock_mongo_db["azure_billing_vectors"].count_documents({})
    assert count == 1


@pytest.mark.asyncio
async def test_semantic_search_builds_pipeline(monkeypatch, mock_openai, mock_mongo_db):
    captured = {}

    def fake_aggregate(pipeline):
        captured["pipeline"] = pipeline
        return [{"document_type": "top_spenders", "text": "x", "metadata": {}}]

    # Patch the underlying mongomock collection (the async wrapper is recreated each access)
    monkeypatch.setattr(mock_mongo_db["azure_billing_vectors"]._col, "aggregate", fake_aggregate)
    results = await vectorizer.semantic_search(mock_mongo_db, "why did costs rise?", top_k=5)
    stage = captured["pipeline"][0]["$vectorSearch"]
    assert stage["index"] == "billing_vector_index"
    assert stage["limit"] == 5
    assert stage["numCandidates"] == 50
    assert len(results) == 1


@pytest.mark.asyncio
async def test_semantic_search_with_filters(monkeypatch, mock_openai, mock_mongo_db):
    captured = {}

    def fake_aggregate(pipeline):
        captured["pipeline"] = pipeline
        return []

    monkeypatch.setattr(mock_mongo_db["azure_billing_vectors"]._col, "aggregate", fake_aggregate)
    await vectorizer.semantic_search(
        mock_mongo_db, "q", document_types=["advisor_recommendation"], period_filter="2026-05", top_k=3
    )
    stage = captured["pipeline"][0]["$vectorSearch"]
    assert stage["filter"]["document_type"] == {"$in": ["advisor_recommendation"]}
    assert stage["filter"]["metadata.period"] == "2026-05"


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
async def test_run_vectorization_skips_unembedded_docs(monkeypatch, mock_openai, mock_mongo_db):
    """run_vectorization only upserts documents that received embeddings."""
    # Insert some cost summary data so documents are generated
    await mock_mongo_db["azure_cost_summary"].insert_many([
        {"period": "2026-05", "dimension": "ServiceName", "dimension_value": "VM", "total_cost": 4000.0, "currency": "USD", "usage_quantity": 10, "unit_of_measure": "Hours"},
    ])
    # Mock date.today to return a fixed date in the test period.
    # Patch vectorizer.date directly (it was bound at import time, so patching
    # datetime.date on the datetime module wouldn't affect this reference).
    import datetime as dt

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 5, 15)

    monkeypatch.setattr(vectorizer, "date", FakeDate)

    count = await vectorizer.run_vectorization(mock_mongo_db)
    # Should have upserted at least 1 doc (top_spenders + service_cost_detail)
    assert count >= 1
    # All upserted docs should have embeddings
    vectors = await mock_mongo_db["azure_billing_vectors"].find({}).to_list(length=100)
    for v in vectors:
        assert "embedding" in v
        assert len(v["embedding"]) > 0
