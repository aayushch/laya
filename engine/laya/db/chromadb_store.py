"""ChromaDB embedded vector store for semantic memory."""

from __future__ import annotations

from typing import Any

import chromadb
import structlog
from chromadb import Collection, Documents, Embeddings, EmbeddingFunction

from laya.config import LAYA_DATA_DIR

log = structlog.get_logger()

CHROMADB_DIR = LAYA_DATA_DIR / "chromadb"
COLLECTION_NAME = "laya_memory"

# Module-level singletons (same pattern as sqlite.py)
_client: chromadb.ClientAPI | None = None
_collection: Collection | None = None
_embedding_model: Any = None  # Lazy-loaded SentenceTransformer

# Task prefixes for nomic-embed-text (improves retrieval quality)
_DOC_PREFIX = "search_document: "
_QUERY_PREFIX = "search_query: "


class LayaDocumentEmbeddingFunction(EmbeddingFunction[Documents]):
    """Embedding function for indexing documents (uses document prefix)."""

    def __call__(self, input: Documents) -> Embeddings:
        model = _get_embedding_model()
        prefixed = [f"{_DOC_PREFIX}{doc}" for doc in input]
        embeddings = model.encode(prefixed).tolist()
        return embeddings


class LayaQueryEmbeddingFunction(EmbeddingFunction[Documents]):
    """Embedding function for search queries (uses query prefix)."""

    def __call__(self, input: Documents) -> Embeddings:
        model = _get_embedding_model()
        prefixed = [f"{_QUERY_PREFIX}{doc}" for doc in input]
        embeddings = model.encode(prefixed).tolist()
        return embeddings


def _get_embedding_model() -> Any:
    """Lazy-load the sentence-transformers model (heavy import, ~2s first load)."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        _embedding_model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
        log.info("embedding_model_loaded", model="nomic-embed-text-v1.5")
    return _embedding_model


def connect_chromadb() -> Collection:
    """Initialize ChromaDB persistent client and return the collection."""
    global _client, _collection

    CHROMADB_DIR.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(CHROMADB_DIR))
    _collection = _client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
        embedding_function=LayaDocumentEmbeddingFunction(),
    )

    log.info("chromadb_connected", path=str(CHROMADB_DIR), collection=COLLECTION_NAME)
    return _collection


def get_collection() -> Collection:
    """Get the ChromaDB collection. Raises if not connected."""
    if _collection is None:
        raise RuntimeError("ChromaDB not initialized. Call connect_chromadb() first.")
    return _collection


def disconnect_chromadb() -> None:
    """Clean up ChromaDB resources."""
    global _client, _collection
    _client = None
    _collection = None
    log.info("chromadb_disconnected")


def is_chromadb_healthy() -> bool:
    """Quick health check."""
    try:
        coll = get_collection()
        coll.count()
        return True
    except Exception:
        return False


async def embed_document(
    doc_id: str,
    text: str,
    metadata: dict[str, Any],
) -> None:
    """Embed a single document into the laya_memory collection.

    Metadata should include: source_event_id, source_platform,
    entity_refs, persona, timestamp, content_type.
    """
    collection = get_collection()
    collection.upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[metadata],
    )
    log.debug("document_embedded", doc_id=doc_id, content_type=metadata.get("content_type"))


async def embed_document_chunked(
    doc_id: str,
    text: str,
    metadata: dict[str, Any],
) -> int:
    """Embed a document, chunking if it exceeds ~400 tokens.

    Returns number of chunks created.
    """
    from laya.db.chunking import chunk_text

    chunks = chunk_text(text)
    if len(chunks) == 1:
        await embed_document(doc_id, text, metadata)
        return 1

    # Delete any previous chunks for this doc
    collection = get_collection()
    try:
        existing = collection.get(where={"parent_id": doc_id})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass

    for i, chunk in enumerate(chunks):
        chunk_meta = {
            **metadata,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "parent_id": doc_id,
        }
        await embed_document(f"{doc_id}_chunk_{i}", chunk, chunk_meta)

    log.debug("document_chunked", doc_id=doc_id, chunks=len(chunks))
    return len(chunks)


async def delete_document(doc_id: str) -> None:
    """Remove a document from the laya_memory collection by ID."""
    collection = get_collection()
    collection.delete(ids=[doc_id])
    log.debug("document_deleted", doc_id=doc_id)


async def memory_search(
    query: str,
    n_results: int = 3,
    where: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic similarity search on past events/content.

    Uses query-prefixed embeddings for better retrieval quality.
    Returns list of dicts with keys: id, document, metadata, distance.
    """
    collection = get_collection()

    # Embed query with query prefix
    query_fn = LayaQueryEmbeddingFunction()
    query_embeddings = query_fn([query])

    kwargs: dict[str, Any] = {
        "query_embeddings": query_embeddings,
        "n_results": n_results,
    }
    if where:
        kwargs["where"] = where

    try:
        results = collection.query(**kwargs)
    except Exception as e:
        log.warning("memory_search_failed", error=str(e))
        return []

    # Flatten ChromaDB's nested result format
    docs: list[dict[str, Any]] = []
    if results and results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            docs.append(
                {
                    "id": doc_id,
                    "document": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results["distances"] else 0.0,
                }
            )

    return docs
