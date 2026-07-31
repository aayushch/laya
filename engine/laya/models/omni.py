# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Omni data models — rolling cross-platform summary."""

from __future__ import annotations

from pydantic import BaseModel


class OmniItemLive(BaseModel):
    """Current state of an item's source cards, computed at read time."""

    max_priority: str | None = None       # highest priority among non-terminal cards
    all_resolved: bool = False
    resolved_count: int = 0
    missing_count: int = 0                # source_cards with no row (archived/purged)
    oldest_created_at: str | None = None  # drives the triage age column
    platform_counts: dict[str, int] = {}


class OmniItem(BaseModel):
    """A single item in an Omni summary section."""

    text: str
    source_cards: list[str] = []  # card_ids for drill-down
    platforms: list[str] = []  # cross-cutting platform tags
    priority: str = "MEDIUM"  # CRITICAL / HIGH / MEDIUM / LOW
    pinned: bool = False
    bookmarked: bool = False
    entity_id: str | None = None  # for entity-level fusion in recent section
    # entity_ids carries ALL contributing entities for a synthesized (aggregate)
    # item. It is the stable join key that lets a later resynthesis correlate a
    # resolved/de-escalated entity with the aggregate bullet it belongs to —
    # source_cards alone don't survive correlation because a resolving event
    # produces a NEW card_id on the same entity_id.
    entity_ids: list[str] = []
    space_id: str = "default"
    # Stable identity across versions — sha1(section|sorted(entity_ids)), see
    # pipeline/omni_change.compute_item_key. Stamped on write and recomputed on
    # read, so snapshots written before migration 072 are addressable too.
    item_key: str | None = None
    # Live state of the item's source cards at read time, decorated by GET /omni.
    # NOT persisted: `priority` above is frozen at synthesis time, so a resolved
    # CRITICAL would still read CRITICAL without this.
    live: OmniItemLive | None = None


class OmniSection(BaseModel):
    """A section within an Omni snapshot."""

    type: str  # "attention" | "recent" | "period" | "milestone"
    label: str | None = None  # e.g. "Sprint 14 (Mar 25 – Apr 7)"
    items: list[OmniItem] = []


class OmniStats(BaseModel):
    """Metadata about the snapshot generation."""

    events_processed: int = 0
    cards_acted_on: int = 0
    compression_ratio: float = 0.0


class OmniSnapshot(BaseModel):
    """A complete Omni snapshot — the primary data object."""

    snapshot_id: str
    space_id: str = "default"
    version: int = 1
    generated_at: str = ""
    snapshot_type: str = "scheduled"  # "incremental" | "scheduled" | "manual"
    sections: list[OmniSection] = []
    stats: OmniStats = OmniStats()


class OmniPin(BaseModel):
    """A pinned item that survives resynthesis compression."""

    pin_id: str
    space_id: str = "default"
    item_text: str = ""
    source_card_ids: list[str] = []
    platforms: list[str] = []
    pinned_at: str = ""
