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
    docs = [{"text": f"doc {i}", "metadata": {}} for i in range(250)]
    result = await vectorizer.embed_documents(docs)
    # 250 docs -> ceil(250/100) = 3 batches
    assert mock_openai.embeddings.create.await_count == 3
    assert all(len(d["embedding"]) == 1536 for d in result)


@pytest.mark.asyncio
async def test_embed_documents_sleeps_between_batches(monkeypatch, mock_openai):
    sleep_mock = AsyncMock()
    monkeypatch.setattr(vectorizer.asyncio, "sleep", sleep_mock)
    docs = [{"text": f"doc {i}", "metadata": {}} for i in range(150)]
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
