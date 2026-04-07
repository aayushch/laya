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
_embedding_model: Any = None  # Lazy-loaded SentenceTransformer (or None if unavailable)
_embedding_backend: str = "unknown"  # "nomic", "mpnet", "minilm", or "chromadb_default"

# Supported embedding models for benchmarking.
# Changing model requires a fresh ChromaDB collection (delete ~/.laya/data/chromadb/).
EMBEDDING_MODELS = {
    "nomic": {
        "name": "nomic-ai/nomic-embed-text-v1.5",
        # Pin to exact HuggingFace revision to prevent silent weight changes
        # that would break existing ChromaDB vectors.
        "revision": "e5cf08aadaa33385f5990def41f7a23405aec398",
        "dimensions": 768,
        "trust_remote_code": True,
        # Nomic uses asymmetric task prefixes for retrieval
        "doc_prefix": "search_document: ",
        "query_prefix": "search_query: ",
    },
    "mpnet": {
        "name": "sentence-transformers/all-mpnet-base-v2",
        "revision": "e8c3b32edf5434bc2275fc9bab85f82640a19130",
        "dimensions": 768,
        "trust_remote_code": False,
        # STS models don't use task prefixes — symmetric similarity
        "doc_prefix": "",
        "query_prefix": "",
    },
    "minilm": {
        "name": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "c9745ed1d9f207416be6d2e6f8de32d1f16199bf",
        "dimensions": 384,
        "trust_remote_code": False,
        "doc_prefix": "",
        "query_prefix": "",
    },
}

# Active model config — set by _choose_embedding_function()
_active_model_config: dict | None = None


def _get_configured_model_key() -> str:
    """Read embedding model selection from settings.json, default to 'nomic'."""
    try:
        from laya.config import load_settings
        settings = load_settings()
        key = settings.get("embedding_model", "nomic")
        if key in EMBEDDING_MODELS:
            return key
        log.warning("unknown_embedding_model", model=key, fallback="nomic")
    except Exception:
        pass
    return "nomic"


def _has_sentence_transformers() -> bool:
    """Check if sentence-transformers (and torch) are importable."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


class LayaDocumentEmbeddingFunction(EmbeddingFunction[Documents]):
    """Embedding function for indexing documents."""

    def __call__(self, input: Documents) -> Embeddings:
        model = _get_embedding_model()
        prefix = _active_model_config["doc_prefix"] if _active_model_config else ""
        if prefix:
            input = [f"{prefix}{doc}" for doc in input]
        embeddings = model.encode(input).tolist()
        return embeddings


class LayaQueryEmbeddingFunction(EmbeddingFunction[Documents]):
    """Embedding function for search queries."""

    def __call__(self, input: Documents) -> Embeddings:
        model = _get_embedding_model()
        prefix = _active_model_config["query_prefix"] if _active_model_config else ""
        if prefix:
            input = [f"{prefix}{doc}" for doc in input]
        embeddings = model.encode(input).tolist()
        return embeddings


def _get_embedding_model() -> Any:
    """Lazy-load the sentence-transformers model (heavy import, ~2s first load)."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        config = _active_model_config or EMBEDDING_MODELS["nomic"]
        kwargs: dict[str, Any] = {}
        if config.get("trust_remote_code"):
            kwargs["trust_remote_code"] = True
        if config.get("revision"):
            kwargs["revision"] = config["revision"]
        _embedding_model = SentenceTransformer(config["name"], **kwargs)
        log.info("embedding_model_loaded", model=config["name"])
    return _embedding_model


def _choose_embedding_function() -> EmbeddingFunction[Documents] | None:
    """Select the best available embedding function.

    Reads model choice from settings.json ("embedding_model": "nomic"|"mpnet"|"minilm").
    Falls back to ChromaDB built-in default if sentence-transformers is unavailable.
    """
    global _embedding_backend, _active_model_config

    if _has_sentence_transformers():
        model_key = _get_configured_model_key()
        _active_model_config = EMBEDDING_MODELS[model_key]
        _embedding_backend = model_key
        log.info(
            "embedding_backend_selected",
            backend=model_key,
            model=_active_model_config["name"],
            dimensions=_active_model_config["dimensions"],
        )
        return LayaDocumentEmbeddingFunction()
    else:
        _embedding_backend = "chromadb_default"
        _active_model_config = None
        log.info(
            "embedding_backend_selected",
            backend="chromadb_default",
            model="all-MiniLM-L6-v2",
            reason="sentence-transformers not available, using ChromaDB built-in",
        )
        return None


def get_embedding_info() -> dict[str, str]:
    """Return info about the active embedding backend (for health/status endpoints)."""
    if _active_model_config:
        return {
            "backend": _embedding_backend,
            "model": _active_model_config["name"],
            "dimensions": str(_active_model_config["dimensions"]),
            "status": "active",
        }
    elif _embedding_backend == "chromadb_default":
        return {
            "backend": "chromadb_default",
            "model": "all-MiniLM-L6-v2",
            "dimensions": "384",
            "status": "fallback",
        }
    return {
        "backend": "unknown",
        "model": "unknown",
        "dimensions": "unknown",
        "status": "not_initialized",
    }


def connect_chromadb() -> Collection:
    """Initialize ChromaDB persistent client and return the collection."""
    global _client, _collection

    CHROMADB_DIR.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(CHROMADB_DIR))

    embedding_fn = _choose_embedding_function()

    kwargs: dict[str, Any] = {
        "name": COLLECTION_NAME,
        "metadata": {
            "hnsw:space": "cosine",
            "hnsw:construction_ef": 200,
            "hnsw:M": 32,
            "hnsw:search_ef": 150,
        },
    }
    if embedding_fn is not None:
        kwargs["embedding_function"] = embedding_fn

    _collection = _client.get_or_create_collection(**kwargs)

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
    max_distance: float | None = None,
) -> list[dict[str, Any]]:
    """Semantic similarity search on past events/content.

    When using nomic embeddings, applies query prefix for better retrieval.
    When using ChromaDB default, passes the query as-is (ChromaDB handles embedding).

    Args:
        query: Search query text.
        n_results: Maximum results to return from ChromaDB.
        where: Optional metadata filter dict.
        max_distance: Optional cosine distance threshold. Results with distance
            above this value are filtered out (lower = more similar).

    Returns list of dicts with keys: id, document, metadata, distance.
    """
    collection = get_collection()

    kwargs: dict[str, Any] = {"n_results": n_results}
    if where:
        kwargs["where"] = where

    if _active_model_config is not None:
        # Use our model (with appropriate prefix handling) for query embedding
        query_fn = LayaQueryEmbeddingFunction()
        query_embeddings = query_fn([query])
        kwargs["query_embeddings"] = query_embeddings
    else:
        # ChromaDB default: pass query text and let it embed internally
        kwargs["query_texts"] = [query]

    try:
        results = collection.query(**kwargs)
    except Exception as e:
        log.warning("memory_search_failed", error=str(e))
        return []

    # Flatten ChromaDB's nested result format
    docs: list[dict[str, Any]] = []
    if results and results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results["distances"] else 0.0
            if max_distance is not None and distance > max_distance:
                continue
            docs.append(
                {
                    "id": doc_id,
                    "document": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": distance,
                }
            )

    return docs
