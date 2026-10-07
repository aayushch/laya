# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for ChromaDB store operations."""

import pytest

from laya.db.chromadb_store import (
    connect_chromadb,
    disconnect_chromadb,
    embed_document,
    get_collection,
    is_chromadb_healthy,
    memory_search,
)


@pytest.fixture
def chromadb_collection(tmp_path, request):
    """Set up an in-memory ChromaDB collection for testing."""
    import chromadb
    from laya.db import chromadb_store

    # Use ephemeral client for tests (no disk), unique name per test
    client = chromadb.EphemeralClient()
    collection_name = f"test_{request.node.name}"
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # Patch the module-level singletons
    original_client = chromadb_store._client
    original_collection = chromadb_store._collection
    chromadb_store._client = client
    chromadb_store._collection = collection

    yield collection

    # Restore
    chromadb_store._client = original_client
    chromadb_store._collection = original_collection


@pytest.mark.network
@pytest.mark.asyncio
async def test_embed_and_search(chromadb_collection):
    """Embed a document and find it via semantic search."""
    await embed_document(
        doc_id="evt_test-001",
        text="NullPointerException in PaymentService when customer ID is null",
        metadata={
            "source_event_id": "evt_test-001",
            "source_platform": "jira",
            "entity_refs": "BUG-1234",
            "persona": "ENGINEER",
            "timestamp": "2026-02-22T14:30:00Z",
            "content_type": "event",
        },
    )

    results = await memory_search("payment service null pointer exception")
    assert len(results) >= 1
    assert results[0]["id"] == "evt_test-001"
    assert results[0]["metadata"]["source_platform"] == "jira"


@pytest.mark.asyncio
async def test_search_empty_collection(chromadb_collection):
    """Search on empty collection returns empty list."""
    results = await memory_search("something random")
    assert results == []


@pytest.mark.network
@pytest.mark.asyncio
async def test_embed_multiple_and_search(chromadb_collection):
    """Embed multiple docs and verify search relevance."""
    await embed_document(
        doc_id="evt_1",
        text="PaymentService throws NullPointerException on null customer ID",
        metadata={"source_event_id": "evt_1", "source_platform": "jira", "content_type": "event",
                  "entity_refs": "", "persona": "ENGINEER", "timestamp": "2026-02-22T14:00:00Z"},
    )
    await embed_document(
        doc_id="evt_2",
        text="Slack discussion about team lunch plans for Friday",
        metadata={"source_event_id": "evt_2", "source_platform": "slack", "content_type": "event",
                  "entity_refs": "", "persona": "OPS", "timestamp": "2026-02-22T14:10:00Z"},
    )
    await embed_document(
        doc_id="evt_3",
        text="Build failed for payments-service repository on main branch",
        metadata={"source_event_id": "evt_3", "source_platform": "bitbucket", "content_type": "event",
                  "entity_refs": "", "persona": "ENGINEER", "timestamp": "2026-02-22T14:20:00Z"},
    )

    # Search for payment-related events
    results = await memory_search("payment service error", n_results=2)
    assert len(results) == 2
    # The most relevant result should be about PaymentService
    result_ids = [r["id"] for r in results]
    assert "evt_1" in result_ids


@pytest.mark.network
@pytest.mark.asyncio
async def test_upsert_updates_document(chromadb_collection):
    """Upserting with same ID updates the document."""
    await embed_document(
        doc_id="evt_upsert",
        text="Original text",
        metadata={"source_event_id": "evt_upsert", "source_platform": "jira", "content_type": "event",
                  "entity_refs": "", "persona": "ENGINEER", "timestamp": "2026-02-22T14:00:00Z"},
    )
    await embed_document(
        doc_id="evt_upsert",
        text="Updated text about database migration issues",
        metadata={"source_event_id": "evt_upsert", "source_platform": "jira", "content_type": "event",
                  "entity_refs": "", "persona": "ENGINEER", "timestamp": "2026-02-22T14:00:00Z"},
    )

    results = await memory_search("database migration")
    assert len(results) >= 1
    assert results[0]["id"] == "evt_upsert"


def test_health_check(chromadb_collection):
    """Health check returns True when collection is available."""
    assert is_chromadb_healthy() is True


class TestConfiguredModelKey:
    """Default embedding model selection (#22)."""

    def test_fresh_store_defaults_to_mpnet(self, monkeypatch):
        from laya.db import chromadb_store

        monkeypatch.setattr("laya.config.load_settings", lambda: {})
        assert chromadb_store._get_configured_model_key() == "mpnet"

    def test_existing_vectors_keep_legacy_nomic(self, monkeypatch):
        from laya.db import chromadb_store

        monkeypatch.setattr("laya.config.load_settings", lambda: {})
        assert chromadb_store._get_configured_model_key(has_existing_vectors=True) == "nomic"

    def test_explicit_setting_wins(self, monkeypatch):
        from laya.db import chromadb_store

        monkeypatch.setattr("laya.config.load_settings", lambda: {"embedding_model": "mpnet"})
        assert chromadb_store._get_configured_model_key(has_existing_vectors=True) == "mpnet"
        monkeypatch.setattr("laya.config.load_settings", lambda: {"embedding_model": "nomic"})
        assert chromadb_store._get_configured_model_key() == "nomic"


def test_connect_detects_existing_vectors(tmp_path, monkeypatch):
    """connect_chromadb passes has_existing_vectors based on the persisted collection."""
    import chromadb
    from laya.db import chromadb_store

    monkeypatch.setattr(chromadb_store, "CHROMADB_DIR", tmp_path)
    seen = []
    monkeypatch.setattr(
        chromadb_store, "_choose_embedding_function", lambda has_existing_vectors=False: seen.append(has_existing_vectors)
    )

    chromadb_store.connect_chromadb()
    chromadb_store.get_collection().add(ids=["a"], embeddings=[[0.1, 0.2, 0.3]], documents=["x"])
    chromadb_store.disconnect_chromadb()
    chromadb_store.connect_chromadb()
    chromadb_store.disconnect_chromadb()

    assert seen == [False, True]
