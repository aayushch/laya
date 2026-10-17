# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Corpus documents: user-shared files parsed, chunked, embedded and indexed
so chat, trace and MCP can read them. See engine/docs/corpus-documents.md."""

from laya.documents.ingest import (
    create_from_bytes,
    create_from_path,
    delete_document_cascade,
    recover_stalled_documents,
    run_ingest,
)

__all__ = [
    "create_from_bytes",
    "create_from_path",
    "delete_document_cascade",
    "recover_stalled_documents",
    "run_ingest",
]
