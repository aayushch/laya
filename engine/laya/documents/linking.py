# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Link a document to the work items it mentions.

Three signals, each producing ``document_entity_links`` rows keyed by the
same ``entity_id`` that cards and the entities table use:

- identifiers such as ``PROJ-123`` or ``PR #540`` found in the text and
  matched against ``action_cards`` (method ``identifier``, confidence 1.0);
- the same identifiers found inside ``entities.platform_refs``
  (``entity_ref``, 0.9);
- canonical entity names that appear in the text (``mention``, 0.6).
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from laya.db.sqlite import get_db
from laya.documents.parsers.base import TextUnit

log = structlog.get_logger()

# Stricter than retrieval.IDENTIFIER_RE, which is meant for short user queries
# and would match "Figure 3" or "Q4 2025" in running prose. Requires an
# upper-case project key joined by - or #, or an explicit PR/MR/GH/ISSUE prefix.
DOC_IDENTIFIER_RE = re.compile(
    r"\b([A-Z][A-Z0-9]{1,9})[-#](\d{1,6})\b"
    r"|\b(PR|MR|GH|ISSUE)\s?#?(\d{1,6})\b"
)

MAX_LINKS_PER_DOCUMENT = 100
MAX_ENTITY_REFS_IN_METADATA = 20
_MIN_MENTION_NAME_LEN = 4
_MENTION_SCAN_ENTITIES = 500


def extract_identifiers(units: list[TextUnit]) -> list[tuple[str, int | None]]:
    """Distinct identifiers in the document with the first page each appears on.

    Returns ``(identifier, page)`` with the identifier normalised to
    ``PREFIX-NUMBER`` (``PR 540`` becomes ``PR-540``).
    """
    seen: dict[str, int | None] = {}
    for unit in units:
        for m in DOC_IDENTIFIER_RE.finditer(unit.text):
            prefix = m.group(1) or m.group(3)
            number = m.group(2) or m.group(4)
            ident = f"{prefix.upper()}-{number}"
            if ident not in seen:
                seen[ident] = unit.page
    return list(seen.items())


def identifier_variants(ident: str) -> list[str]:
    """The spellings ``trace._identifier_search`` also tries for one identifier."""
    prefix, _, number = ident.partition("-")
    variants = [
        f"{prefix}-{number}",
        f"{prefix} #{number}",
        f"{prefix}#{number}",
        f"{prefix} {number}",
    ]
    return variants


async def resolve_entity_links(
    space_id: str | None,
    units: list[TextUnit],
    identifiers: list[tuple[str, int | None]],
) -> list[dict[str, Any]]:
    """Resolve identifiers and name mentions to entity ids.

    Deduplicates on ``entity_id`` keeping the highest-confidence link and caps
    the result at ``MAX_LINKS_PER_DOCUMENT``.
    """
    db = await get_db()
    links: dict[str, dict[str, Any]] = {}

    def offer(entity_id: str, link: dict[str, Any]) -> None:
        current = links.get(entity_id)
        if current is None or link["confidence"] > current["confidence"]:
            if current is not None:
                link["mention_count"] = max(link.get("mention_count", 1), current.get("mention_count", 1))
            links[entity_id] = {"entity_id": entity_id, **link}
        else:
            current["mention_count"] = current.get("mention_count", 1) + link.get("mention_count", 1)

    # 1. Identifiers against cards.
    for ident, page in identifiers:
        conditions: list[str] = []
        params: list[Any] = []
        for v in identifier_variants(ident):
            conditions.append("(c.source_ref LIKE ? OR c.entity_id LIKE ? OR c.header LIKE ?)")
            params.extend([f"%{v}%"] * 3)
        where = " OR ".join(conditions)
        extra = ""
        if space_id:
            extra = " AND c.space_id = ?"
            params.append(space_id)
        rows = await db.execute_fetchall(
            f"SELECT DISTINCT c.entity_id FROM action_cards c WHERE ({where}){extra} AND c.entity_id IS NOT NULL LIMIT 20",
            params,
        )
        for r in rows:
            offer(r["entity_id"], {
                "link_method": "identifier", "confidence": 1.0, "mention_count": 1,
                "first_page": page, "matched_text": ident,
            })

        # 2. Identifiers inside entities.platform_refs.
        ref_rows = await db.execute_fetchall(
            "SELECT entity_id FROM entities WHERE platform_refs LIKE ? LIMIT 20",
            (f'%"{ident}"%',),
        )
        for r in ref_rows:
            offer(r["entity_id"], {
                "link_method": "entity_ref", "confidence": 0.9, "mention_count": 1,
                "first_page": page, "matched_text": ident,
            })

    # 3. Canonical-name mentions.
    text_by_unit = [(u.text.lower(), u.page) for u in units]
    name_rows = await db.execute_fetchall(
        "SELECT entity_id, canonical_name FROM entities ORDER BY updated_at DESC LIMIT ?",
        (_MENTION_SCAN_ENTITIES,),
    )
    for r in name_rows:
        name = (r["canonical_name"] or "").strip()
        if len(name) < _MIN_MENTION_NAME_LEN:
            continue
        needle = name.lower()
        count = 0
        first_page: int | None = None
        for text, page in text_by_unit:
            hits = text.count(needle)
            if hits:
                count += hits
                if first_page is None:
                    first_page = page
        if count:
            offer(r["entity_id"], {
                "link_method": "mention", "confidence": 0.6, "mention_count": count,
                "first_page": first_page, "matched_text": name,
            })

    ordered = sorted(links.values(), key=lambda l: (-l["confidence"], -l.get("mention_count", 1)))
    return ordered[:MAX_LINKS_PER_DOCUMENT]


def entity_refs_csv(links: list[dict[str, Any]]) -> str:
    """CSV of linked entity ids for Chroma metadata, capped."""
    return ",".join(l["entity_id"] for l in links[:MAX_ENTITY_REFS_IN_METADATA])
