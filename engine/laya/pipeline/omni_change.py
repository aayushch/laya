# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Omni change summaries — what moved between two snapshot versions.

Omni's whole claim is that information *compresses*: an item is synthesized into
`recent`, folds into `period`, is promoted to `milestone`, or drops out entirely
once its subjects resolve. None of that was ever recorded — `_compute_delta()`
captured additions on incremental writes only, and a resynthesis (the write where
folding actually happens) stored the new full structure with no diff at all.

This module computes that diff and shapes it into the `change_summary_json`
column added by migration 072:

    {
      "added":    [{item_key, section, text, source_count, platforms}],
      "folded":   [{item_key, from_section, to_section, from_text, to_text}],
      "resolved": [{item_key, section, text, entity_ids, resolved_at}],
      "counts":   {"added": n, "folded": n, "resolved": n}
    }

Correlation across versions is by ``entity_ids`` — the field the Omni prompt
already requires on every item precisely because ``source_cards`` cannot survive
it (a resolving event produces a NEW card_id on the SAME entity_id).
"""

from __future__ import annotations

import hashlib
import json

# The compression chain, in order. An item that appears in a LATER section than
# it did before has been folded/promoted; earlier is not a thing Omni does.
SECTION_CHAIN = ["attention", "recent", "period", "milestone"]
_SECTION_RANK = {name: i for i, name in enumerate(SECTION_CHAIN)}


def section_rank(section_type: str | None) -> int:
    """Position of a section in the compression chain (-1 when unknown)."""
    return _SECTION_RANK.get(section_type or "", -1)


# ---------------------------------------------------------------------------
# Item keys
# ---------------------------------------------------------------------------


def compute_item_key(
    section_type: str,
    entity_ids: list[str] | None,
    source_cards: list[str] | None = None,
    text: str | None = None,
) -> str:
    """Stable per-item identity, reproducible across versions and processes.

    ``sha1(section + "|" + sorted(entity_ids))`` truncated to 12 hex chars.
    Entity ids are the join key because they outlive the cards that carry them.

    Fallback chain, for snapshots the prompt's entity_ids requirement didn't
    reach (older models, or the backfill in ``_resynthesize_space`` finding no
    cards to backfill from):

      entity_ids → source_cards → text

    The text fallback matters: an item with NEITHER entity_ids nor source_cards
    has no other identity at all, and two of them in the same section would
    otherwise hash identically — which is a duplicate key, not a theoretical
    one. Text is less stable across versions than ids, but such an item has
    nothing to drill into anyway, so a key that changes when the text does costs
    nothing and a colliding key costs the whole page.
    """
    eids = sorted({e for e in (entity_ids or []) if e})
    if eids:
        payload = f"{section_type}|{','.join(eids)}"
    else:
        cards = sorted({c for c in (source_cards or []) if c})
        if cards:
            payload = f"{section_type}|cards:{','.join(cards)}"
        else:
            payload = f"{section_type}|text:{(text or '').strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def item_key_of(section_type: str, item: dict) -> str:
    """The item's stored key, or a freshly computed one.

    Prefers what ``decorate_item_keys`` already stamped, so the changelog names
    items by exactly the key the drill-down link resolves — including any
    disambiguating suffix applied below.
    """
    stored = item.get("item_key")
    if stored:
        return str(stored)
    return compute_item_key(
        section_type, item.get("entity_ids"), item.get("source_cards"), item.get("text")
    )


def decorate_item_keys(sections: list[dict]) -> list[dict]:
    """Stamp ``item_key`` onto every item of every section, in place.

    Called on write so the key is persisted, and again on read so snapshots
    written before migration 072 still expose one (same input → same key, so
    a stored key and a recomputed key are indistinguishable).

    Keys are forced unique *within a section*. Uniqueness is not a nicety: the
    UI keys its lists by item_key, and a repeat is a hard render error that takes
    the whole board down — which is exactly what two content-free milestone items
    (no entity_ids, no source_cards) did. The suffix is applied in item order, so
    the same stored snapshot always decorates identically.
    """
    for section in sections or []:
        stype = section.get("type") or ""
        seen: dict[str, int] = {}
        for item in section.get("items", []) or []:
            base = compute_item_key(
                stype,
                item.get("entity_ids"),
                item.get("source_cards"),
                item.get("text"),
            )
            count = seen.get(base, 0)
            seen[base] = count + 1
            item["item_key"] = base if count == 0 else f"{base}-{count}"
    return sections


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


def _entity_set(item: dict) -> set[str]:
    eids = {e for e in (item.get("entity_ids") or []) if e}
    single = item.get("entity_id")
    if single:
        eids.add(single)
    return eids


def _card_set(item: dict) -> set[str]:
    return {c for c in (item.get("source_cards") or []) if c}


def _flatten(sections: list[dict]) -> list[tuple[str, dict]]:
    """[(section_type, item)] across all sections, chain order irrelevant."""
    out: list[tuple[str, dict]] = []
    for section in sections or []:
        stype = section.get("type") or ""
        for item in section.get("items", []) or []:
            out.append((stype, item))
    return out


def subject_matches(a: dict, b: dict) -> bool:
    """True when two items (from different versions) describe the same subject.

    Entity overlap is the real test; card overlap is the fallback for items whose
    entity_ids were never populated. Used by the lineage walk, which follows an
    item across versions where its section — and therefore its item_key — changes.
    """
    if _entity_set(a) & _entity_set(b):
        return True
    return bool(_card_set(a) & _card_set(b))


def _find_match(
    target: dict, candidates: list[tuple[str, dict]], used: set[int]
) -> int | None:
    """Index of the best unused candidate describing the same subject as `target`.

    Entity overlap first (the durable key), then card overlap as a fallback for
    items whose entity_ids never got populated. Highest overlap wins so an
    aggregate that absorbed several lines matches the one it shares most with,
    rather than whichever happened to be scanned first.
    """
    t_eids = _entity_set(target)
    t_cards = _card_set(target)
    best_idx: int | None = None
    best_score = 0

    for idx, (_stype, cand) in enumerate(candidates):
        if idx in used:
            continue
        score = len(t_eids & _entity_set(cand)) * 100
        if not score:
            score = len(t_cards & _card_set(cand))
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


def empty_change_summary() -> dict:
    return {"added": [], "folded": [], "resolved": [], "counts": {"added": 0, "folded": 0, "resolved": 0}}


def _finalize(summary: dict) -> dict:
    summary["counts"] = {
        "added": len(summary["added"]),
        "folded": len(summary["folded"]),
        "resolved": len(summary["resolved"]),
    }
    return summary


def compute_incremental_change_summary(
    added_items: list[dict], section_type: str = "recent"
) -> dict:
    """Change summary for an incremental (queue-append) write.

    Only additions are possible on this path — the queue appends cards to
    `recent` and fuses them into existing items; it never folds or resolves.
    Fused updates are deliberately not emitted: they rewrite a line's counts
    rather than change the set of lines, and the rail's three kinds
    (added/folded/resolved) are about set membership.
    """
    summary = empty_change_summary()
    for item in added_items or []:
        summary["added"].append({
            "item_key": item_key_of(section_type, item),
            "section": section_type,
            "text": item.get("text", ""),
            "source_count": len(item.get("source_cards") or []),
            "platforms": list(item.get("platforms") or []),
        })
    return _finalize(summary)


def compute_resynthesis_change_summary(
    prior_sections: list[dict],
    new_sections: list[dict],
    card_meta: dict[str, dict],
    terminal_statuses: set[str],
    resolved_at_by_entity: dict[str, str] | None = None,
) -> dict:
    """Change summary for a resynthesis (full base) write.

    Rules, matching the handoff:
      - in new, not in prior                      → added
      - in prior, not in new, all cards terminal  → resolved
      - in prior section X, in new section Y > X  → folded
      - in prior, not in new, not resolved        → folded with to_section=None
        (dropped by compression rather than by being finished)

    `card_meta` is `{card_id: {status, ...}}` as returned by
    `pipeline.omni._fetch_card_meta`; `terminal_statuses` is passed in rather than
    imported so this module stays free of the lifecycle import cycle.
    """
    summary = empty_change_summary()
    prior = _flatten(prior_sections)
    new = _flatten(new_sections)
    resolved_at_by_entity = resolved_at_by_entity or {}

    matched_new: set[int] = set()

    for prior_section, prior_item in prior:
        idx = _find_match(prior_item, new, matched_new)
        prior_key = item_key_of(prior_section, prior_item)

        if idx is None:
            # Gone from the snapshot. Finished, or compressed away?
            cards = [c for c in (prior_item.get("source_cards") or []) if c]
            known = [card_meta[c] for c in cards if c in card_meta]
            all_resolved = bool(known) and all(
                m.get("status") in terminal_statuses for m in known
            )
            eids = sorted(_entity_set(prior_item))
            if all_resolved:
                resolved_at = None
                for eid in eids:
                    if resolved_at_by_entity.get(eid):
                        resolved_at = resolved_at_by_entity[eid]
                        break
                if resolved_at is None:
                    stamps = [m.get("resolved_at") for m in known if m.get("resolved_at")]
                    resolved_at = max(stamps) if stamps else None
                summary["resolved"].append({
                    "item_key": prior_key,
                    "section": prior_section,
                    "text": prior_item.get("text", ""),
                    "entity_ids": eids,
                    "resolved_at": resolved_at,
                })
            else:
                summary["folded"].append({
                    "item_key": prior_key,
                    "from_section": prior_section,
                    "to_section": None,
                    "from_text": prior_item.get("text", ""),
                    "to_text": None,
                })
            continue

        matched_new.add(idx)
        new_section, new_item = new[idx]
        if section_rank(new_section) > section_rank(prior_section):
            summary["folded"].append({
                "item_key": item_key_of(new_section, new_item),
                "from_section": prior_section,
                "to_section": new_section,
                "from_text": prior_item.get("text", ""),
                "to_text": new_item.get("text", ""),
            })

    for idx, (new_section, new_item) in enumerate(new):
        if idx in matched_new:
            continue
        summary["added"].append({
            "item_key": item_key_of(new_section, new_item),
            "section": new_section,
            "text": new_item.get("text", ""),
            "source_count": len(new_item.get("source_cards") or []),
            "platforms": list(new_item.get("platforms") or []),
        })

    return _finalize(summary)


# ---------------------------------------------------------------------------
# Merging across a version range
# ---------------------------------------------------------------------------

# Later state wins when the same item appears in more than one kind across the
# range: an item added at v1220 and resolved at v1226 reads as resolved, not
# both. Rank orders the kinds by finality.
_KIND_RANK = {"added": 0, "folded": 1, "resolved": 2}


def merge_change_summaries(summaries: list[dict]) -> dict:
    """Collapse per-version summaries (oldest → newest) into one range summary.

    The rail compares an arbitrary base version against the displayed one, which
    can span many writes. Entries are keyed by item_key so a line that was added
    and then folded within the range is reported once, in its final state.
    """
    latest: dict[str, tuple[int, str, dict]] = {}
    order: list[str] = []

    for version_index, summary in enumerate(summaries or []):
        if not summary:
            continue
        for kind in ("added", "folded", "resolved"):
            for entry in summary.get(kind) or []:
                key = entry.get("item_key") or f"{kind}:{entry.get('text', '')}"
                prev = latest.get(key)
                if prev is None:
                    order.append(key)
                    latest[key] = (version_index, kind, entry)
                    continue
                _prev_v, prev_kind, prev_entry = prev
                # A later, more final kind supersedes. Same kind → keep the
                # newest text (an aggregate's counts get rewritten as it grows).
                if _KIND_RANK[kind] >= _KIND_RANK[prev_kind]:
                    if kind == "folded" and prev_kind == "folded":
                        # Chain two folds into one: recent → period → milestone
                        # reads as recent → milestone.
                        entry = {
                            **entry,
                            "from_section": prev_entry.get("from_section"),
                            "from_text": prev_entry.get("from_text"),
                        }
                    latest[key] = (version_index, kind, entry)

    merged = empty_change_summary()
    for key in order:
        _v, kind, entry = latest[key]
        merged[kind].append(entry)
    return _finalize(merged)


def parse_change_summary(raw: str | None) -> dict | None:
    """Decode a stored change_summary_json cell, tolerating legacy/NULL rows."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    for kind in ("added", "folded", "resolved"):
        parsed.setdefault(kind, [])
    parsed.setdefault(
        "counts",
        {kind: len(parsed[kind]) for kind in ("added", "folded", "resolved")},
    )
    return parsed


def fold_counts_by_section(summary: dict | None) -> dict[str, int]:
    """Per-source-section fold counts, for the funnel's between-band annotations.

    Keyed by the section the items folded OUT of, since the annotation renders
    below that band.
    """
    counts: dict[str, int] = {}
    for entry in (summary or {}).get("folded") or []:
        src = entry.get("from_section")
        if src:
            counts[src] = counts.get(src, 0) + 1
    return counts


def resolved_counts_by_section(summary: dict | None) -> dict[str, int]:
    """Per-section resolved counts, for the funnel's between-band annotations."""
    counts: dict[str, int] = {}
    for entry in (summary or {}).get("resolved") or []:
        src = entry.get("section")
        if src:
            counts[src] = counts.get(src, 0) + 1
    return counts
