# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Corpus documents REST API: share, list, read, rename, reprocess, delete.

Static paths (``/documents/supported-types``, ``/documents/by-entity``) are
declared before ``/documents/{document_id}`` so they are not captured by it.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from laya.api.cards_common import CARD_SELECT_COLUMNS, _row_to_card
from laya.db.chromadb_store import get_embedding_info, update_document_metadata
from laya.db.sqlite import get_db
from laya.documents import ingest, store
from laya.documents.parsers import supported_extensions
from laya.models.card import CardResponse
from laya.models.document import (
    DocumentChunksResponse,
    DocumentFromPathRequest,
    DocumentListResponse,
    DocumentRelocateRequest,
    DocumentRenameRequest,
    DocumentResponse,
    SupportedTypesResponse,
)

log = structlog.get_logger()
router = APIRouter()


def _backend() -> str | None:
    try:
        return get_embedding_info().get("backend")
    except Exception:
        return None


def _response(row: dict[str, Any], links: list[dict[str, Any]] | None = None) -> DocumentResponse:
    return DocumentResponse(**store.to_document_response(row, current_backend=_backend(), entity_links=links))


def _raise(e: ingest.DocumentError) -> None:
    raise HTTPException(
        status_code=e.status_code,
        detail={"message": e.message, "error_code": e.error_code, **e.extra},
    )


async def _get_or_404(document_id: str) -> dict[str, Any]:
    row = await store.get_document(document_id)
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return row


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/documents", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    space_id: str = Form("default"),
    title: str | None = Form(None),
) -> DocumentResponse:
    """Share a file by uploading its bytes. The engine keeps a copy."""
    filename = file.filename or "upload"

    async def _stream():
        while chunk := await file.read(1 << 20):
            yield chunk

    try:
        row = await ingest.create_from_bytes(
            space_id, filename, _stream(), title=title, content_type=file.content_type,
        )
    except ingest.DocumentError as e:
        _raise(e)
    return _response(row)


@router.post("/documents/from-path", status_code=202)
async def add_document_from_path(body: DocumentFromPathRequest) -> DocumentResponse:
    """Share a file by path. The file is read in place; nothing is copied."""
    try:
        row = await ingest.create_from_path(body.space_id or "default", body.path, title=body.title)
    except ingest.DocumentError as e:
        _raise(e)
    return _response(row)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/documents/supported-types")
async def get_supported_types() -> SupportedTypesResponse:
    return SupportedTypesResponse(extensions=supported_extensions(), max_file_mb=ingest.max_file_bytes() // (1024 * 1024))


@router.get("/documents/by-entity")
async def get_documents_by_entity(entity_id: str, space_id: str | None = None) -> list[dict[str, Any]]:
    links = await store.documents_for_entities({entity_id}, space_id)
    backend = _backend()
    out: list[dict[str, Any]] = []
    for link in links.get(entity_id, []):
        row = await store.get_document(link["document_id"])
        if not row:
            continue
        doc = store.to_document_response(row, current_backend=backend)
        doc.update({
            "link_method": link["link_method"],
            "confidence": link["confidence"],
            "mention_count": link.get("mention_count") or 1,
            "first_page": link.get("first_page"),
        })
        out.append(doc)
    return out


@router.get("/documents")
async def list_documents(
    space_id: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> DocumentListResponse:
    rows, total = await store.list_documents(space_id=space_id, status=status, q=q, limit=limit, offset=offset)
    backend = _backend()
    return DocumentListResponse(
        documents=[DocumentResponse(**store.to_document_response(r, current_backend=backend)) for r in rows],
        total=total,
    )


@router.get("/documents/{document_id}")
async def get_document(document_id: str) -> DocumentResponse:
    row = await _get_or_404(document_id)
    links = await store.links_for_document(document_id)
    return _response(row, links)


@router.get("/documents/{document_id}/chunks")
async def get_document_chunks(
    document_id: str,
    page_from: int | None = None,
    page_to: int | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> DocumentChunksResponse:
    await _get_or_404(document_id)
    if page_from is None and page_to is None:
        total = await store.count_chunks(document_id)
    else:
        total = len(await store.get_chunks(document_id, page_from=page_from, page_to=page_to))
    chunks = await store.get_chunks(document_id, page_from=page_from, page_to=page_to, limit=limit, offset=offset)
    return DocumentChunksResponse(chunks=chunks, total=total)


async def _cards_for_entities(entity_ids: list[str], limit: int = 50) -> list[CardResponse]:
    if not entity_ids:
        return []
    db = await get_db()
    placeholders = ",".join("?" * len(entity_ids))
    rows = await db.execute_fetchall(
        f"""SELECT {CARD_SELECT_COLUMNS}
            FROM action_cards c
            LEFT JOIN events e ON c.event_id = e.event_id
            LEFT JOIN spaces s ON c.space_id = s.space_id
            WHERE c.entity_id IN ({placeholders})
            ORDER BY c.created_at DESC LIMIT ?""",
        [*entity_ids, limit],
    )
    return [_row_to_card(r) for r in rows]


@router.get("/documents/{document_id}/related")
async def get_document_related(document_id: str, entity_id: str | None = None) -> dict[str, Any]:
    """Entities the document links to and the cards attached to them."""
    await _get_or_404(document_id)
    links = await store.links_for_document(document_id)
    if entity_id:
        links = [l for l in links if l["entity_id"] == entity_id]
    cards = await _cards_for_entities([l["entity_id"] for l in links])
    return {"entities": links, "cards": cards}


@router.get("/cards/{card_id}/related-documents")
async def get_card_related_documents(card_id: str) -> list[dict[str, Any]]:
    """Documents linked to the card's entity."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT entity_id, space_id FROM action_cards WHERE card_id = ?", (card_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Card not found")
    entity_id = rows[0]["entity_id"]
    if not entity_id:
        return []
    return await get_documents_by_entity(entity_id, None)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.patch("/documents/{document_id}")
async def rename_document(document_id: str, body: DocumentRenameRequest) -> DocumentResponse:
    row = await _get_or_404(document_id)
    title = body.title.strip()
    await store.update_fields(document_id, title=title)
    try:
        await update_document_metadata(document_id, {"title": title})
    except Exception as e:
        log.warning("document_title_vector_patch_failed", document_id=document_id, error=str(e))
    updated = await store.get_document(document_id) or row
    await ingest._broadcast(updated)
    return _response(updated, await store.links_for_document(document_id))


@router.post("/documents/{document_id}/reprocess", status_code=202)
async def reprocess_document(
    document_id: str,
    stage: str = Query("all", pattern="^(all|embed|summarize|link)$"),
) -> DocumentResponse:
    try:
        row = await ingest.reprocess(document_id, stage)
    except ingest.DocumentError as e:
        _raise(e)
    return _response(row)


@router.post("/documents/{document_id}/resync")
async def resync_document(document_id: str) -> dict[str, Any]:
    try:
        result = await ingest.resync(document_id)
    except ingest.DocumentError as e:
        _raise(e)
    return {"status": result["status"], "document": _response(result["document"])}


@router.post("/documents/{document_id}/relocate")
async def relocate_document(document_id: str, body: DocumentRelocateRequest) -> DocumentResponse:
    try:
        row = await ingest.relocate(document_id, body.path)
    except ingest.DocumentError as e:
        _raise(e)
    return _response(row)


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str) -> dict[str, str]:
    if not await ingest.delete_document_cascade(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "document_id": document_id}
