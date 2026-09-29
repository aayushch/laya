#!/usr/bin/env python3
"""Embed one string with an engine embedding backend and check the result.

Run with the engine's Python from the engine/ directory:

    python ../scripts/check-embedding.py nomic      # default model (needs the ML packages)
    python ../scripts/check-embedding.py default    # ChromaDB built-in ONNX model

Loads the model the same way the engine does, so it fails when an installed
package version cannot run it (for example a transformers release that is
incompatible with the pinned nomic revision). The first run downloads the model.
"""

import importlib.metadata as metadata
import sys


def _versions(*packages: str) -> str:
    return ", ".join(f"{p} {metadata.version(p)}" for p in packages)


def check_default() -> None:
    from chromadb.utils import embedding_functions

    vectors = embedding_functions.DefaultEmbeddingFunction()(["hello world"])
    assert len(vectors) == 1 and len(vectors[0]) == 384, "unexpected embedding shape"
    print(f"OK default (ONNX MiniLM), 384 dims [{_versions('chromadb', 'onnxruntime', 'numpy')}]")


def check_model(key: str) -> None:
    from laya.db import chromadb_store

    config = chromadb_store.EMBEDDING_MODELS[key]
    chromadb_store._active_model_config = config
    vectors = chromadb_store.LayaDocumentEmbeddingFunction()(["hello world"])
    assert len(vectors) == 1 and len(vectors[0]) == config["dimensions"], "unexpected embedding shape"
    print(
        f"OK {key} ({config['name']}), {config['dimensions']} dims "
        f"[{_versions('sentence-transformers', 'transformers', 'torch', 'numpy')}]"
    )


def main() -> int:
    sys.path.insert(0, ".")
    backend = sys.argv[1] if len(sys.argv) > 1 else "nomic"
    if backend == "default":
        check_default()
    else:
        check_model(backend)
    return 0


if __name__ == "__main__":
    sys.exit(main())
