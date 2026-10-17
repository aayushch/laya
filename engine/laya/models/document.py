# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for Corpus documents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DocumentEntityLink(BaseModel):
    entity_id: str
    link_method: str
    confidence: float
    mention_count: int = 1
    first_page: int | None = None
    matched_text: str | None = None
    card_count: int = 0


class DocumentResponse(BaseModel):
    document_id: str
    space_id: str
    title: str
    original_filename: str
    file_type: str
    mime_type: str | None = None
    size_bytes: int
    page_count: int | None = None
    chunk_count: int = 0
    char_count: int = 0
    storage_mode: Literal["reference", "copy"] = "reference"
    source_path: str | None = None
    source_state: Literal["copy", "ok", "changed", "missing"] = "ok"
    status: str
    error: str | None = None
    error_code: str | None = None
    summary: str | None = None
    summary_model: str | None = None
    embedding_model: str | None = None
    needs_reembed: bool = False
    created_at: str
    updated_at: str
    indexed_at: str | None = None
    entity_links: list[DocumentEntityLink] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    page_start: int | None = None
    page_end: int | None = None
    heading: str | None = None
    char_count: int


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


class DocumentChunksResponse(BaseModel):
    chunks: list[DocumentChunk]
    total: int


class DocumentFromPathRequest(BaseModel):
    path: str
    space_id: str = "default"
    title: str | None = None


class DocumentRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class DocumentRelocateRequest(BaseModel):
    path: str


class SupportedTypesResponse(BaseModel):
    extensions: list[str]
    max_file_mb: int


class ConversationDocumentsRequest(BaseModel):
    document_ids: list[str] = Field(default_factory=list)
