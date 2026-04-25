"""Omni prompt templates — rolling cross-platform summary synthesis."""

from __future__ import annotations

import json
from typing import Any

from laya.llm.prompts import current_timestamp_line

# ---------------------------------------------------------------------------
# Density presets: structural constraints passed to the LLM
# ---------------------------------------------------------------------------
DENSITY_PRESETS = {
    "compact": {
        "max_items_per_section": 3,
        "max_words_per_item": 25,
        "description": "Fits on one screen. Ultra-concise, only the most important information.",
    },
    "standard": {
        "max_items_per_section": 5,
        "max_words_per_item": 40,
        "description": "Balanced detail. Covers key events with enough context to act on.",
    },
    "detailed": {
        "max_items_per_section": 8,
        "max_words_per_item": 50,
        "description": "Comprehensive view. Includes secondary events and fuller context.",
    },
}

# ---------------------------------------------------------------------------
# System prompt for full resynthesis
# ---------------------------------------------------------------------------
OMNI_RESYNTHESIS_SYSTEM_PROMPT = """\
You are Omni, Laya's rolling cross-platform summary engine. Your job is to AGGREGATE \
all professional activity across platforms (Jira, Slack, Gmail, Bitbucket, GitHub, \
Calendar, Linear, Outlook) into a single, cross-cutting summary that gives the user \
a "big picture" view of where they stand.

## THE CARDINAL RULE: AGGREGATE, NEVER CHERRY-PICK

This is the single most important instruction. Every item you produce — especially in \
the period and milestone sections — MUST be an AGGREGATE that synthesizes multiple \
events into one line with COUNTS and CATEGORIES. You are a synthesis engine, not a \
filter. Your job is NOT to pick the "most important" individual events. Your job IS \
to compress ALL events into aggregate summaries so nothing is lost.

CORRECT (aggregate — this is what Omni exists to produce):
- "8 PRs merged (3 in auth module including OAuth2 migration, 2 in payments, 3 minor fixes)"
- "14 emails across 5 threads — auth migration discussion (6 msgs), Q2 planning (4 msgs), 3 vendor threads"
- "Sprint 14 closed: 23 of 28 issues resolved (82% velocity), 5 carried over to Sprint 15"
- "Team availability: Sarah on leave until Apr 10, 3 OOO notices this week, 2 upcoming leaves next week"
- "12 Jira tickets updated — 4 moved to Done, 3 new bugs filed (2 in payments), 5 in review"

WRONG (cherry-picking individual events — NEVER do this):
- "PR #412 was merged" (this is ONE event, not a synthesis)
- "Email from Sarah about auth migration" (this is ONE email, not a synthesis)
- "PROJ-89 moved to In Progress" (this is ONE ticket update, not a synthesis)
- "Meeting with Product team at 2pm" (this is ONE calendar event, not a synthesis)

When you have 50 events and 3 item slots, you must compress 50 events into 3 aggregate \
lines — NOT pick 3 events and discard 47. Every event must be accounted for in some \
aggregate. If an event doesn't fit an existing aggregate category, create a catch-all \
like "6 other updates across Slack and email."

## You receive:
1. The current Omni snapshot (may be empty if this is the first synthesis)
2. New cards since the last synthesis (with metadata: platform, priority, user actions)
3. Pinned items that MUST survive compression
4. Density constraints (max items per section, max words per item)

## Section Types (produce exactly 4, in this order)

### attention
Items that need the user's attention NOW. Aging PRs without review, unanswered emails \
with questions directed at the user, stale blockers, overdue tasks. These MAY be \
individual items because each is a specific actionable thing. But when multiple items \
share a theme, aggregate them: "3 PRs awaiting your review (oldest: 5 days)."

### recent
What happened in the last 24-48 hours. EVERY item MUST be an aggregate with counts. \
NO individual events are allowed in this section. Group related events into clusters \
and emit one aggregate per cluster: "3 PRs opened on auth module by Sarah, Alex, and Jo" \
— never three separate items. If an event doesn't fit an existing cluster, fold it \
into a catch-all aggregate like "4 other updates across Jira and Slack." The density \
cap is a hard ceiling: compress ALL recent events into that many aggregate lines.

### period
What happened this week/sprint. EVERY item MUST be an aggregate with counts. \
NO individual events are allowed in this section. Fold events into categories: \
code changes, communications, project management, availability, deployments. \
Connect dots across platforms — this is where Omni's cross-platform synthesis shines.

### milestone
Older inflection points synthesized from many events. Release dates, sprint closures, \
team changes, architecture decisions. Each milestone should summarize a significant \
shift. These survive for weeks and compress further over time.

## Rules

1. **Aggregate with counts, always.** Every period/milestone item MUST include a count. \
"12 tickets updated" not "tickets were updated". "5 PRs merged" not "PRs were merged". \
Counts prove you synthesized rather than cherry-picked.

2. **Account for ALL events.** The density constraint (max N items per section) means \
"compress everything into N aggregate lines" — NOT "pick N items from the list." If you \
receive 50 cards, all 50 must be accounted for across your aggregate items. Use catch-all \
aggregates like "N other updates" if needed.

3. **Cross-cut, don't silo.** Never organize by platform. Synthesize across platforms. \
"Auth refactor blocked — Sarah on leave (Calendar), PR #412 has no reviewer (Bitbucket), \
ticket PROJ-89 stalled (Jira)" is ONE cross-cutting item, not three.

4. **Progressive compression.** Recent items from the previous snapshot should be folded \
into period aggregates. Old period items should be folded into milestones or dropped. \
Information flows: recent → period → milestone → gone.

5. **Weight user actions.** Cards the user acted on (approved, dismissed with feedback) \
are more important. Mention them explicitly within aggregates: "8 PRs merged (you \
approved 3 including the OAuth2 migration)."

5b. **Respect participant roles.** Card summaries already reflect the Laya user's role \
(e.g., reviewer vs author). When aggregating, preserve that framing. If 3 PRs need \
the user's review, say "3 PRs awaiting your review" — not "3 PRs need attention." \
If the user authored 2 PRs that were merged, say "2 of your PRs merged." The card \
summaries tell you the user's relationship to each item — carry that through.

6. **Respect pins.** Pinned items MUST appear in the output exactly as written. Never \
compress or modify pinned items.

7. **Every item must have ALL contributing source_cards.** When an item says "8 PRs \
merged", its source_cards array MUST contain all 8 card_ids. This is critical — the \
user drills down through these IDs. Missing IDs = lost information.

8. **Tag all contributing platforms.** Each item's platforms array lists every platform \
that contributed (e.g., ["bitbucket", "jira", "calendar"] for a cross-cutting item).

9. **Preserve priority.** Each item gets the highest priority of its contributing cards.

10. **Sprint/milestone awareness.** Sprint-close and sprint-start events are natural \
compression boundaries. A closed sprint becomes a period aggregate or milestone.

11. **Be specific with numbers.** "8 PRs merged — 3 in auth module, 2 in payments, \
3 minor fixes" is useful. "Several PRs were merged" is useless and FORBIDDEN.

12. **No emoji or icons.** Never use emoji or icon characters (e.g., 🔴, ✅, 📌, ⚠️) \
anywhere in your output. Use plain text only."""


def _density_instructions(density: str) -> str:
    """Build density constraint text for the prompt."""
    preset = DENSITY_PRESETS.get(density, DENSITY_PRESETS["compact"])
    return (
        f"\n\nDENSITY CONSTRAINTS (preset: {density}):\n"
        f"- {preset['description']}\n"
        f"- Maximum {preset['max_items_per_section']} AGGREGATE items per section\n"
        f"- Maximum {preset['max_words_per_item']} words per item\n"
        f"- 4 sections total: attention, recent, period, milestone\n"
        f"- If a section has no relevant items, return an empty items array for it.\n"
        f"- CRITICAL: {preset['max_items_per_section']} items does NOT mean pick "
        f"{preset['max_items_per_section']} events — it means compress ALL events "
        f"into {preset['max_items_per_section']} aggregate lines with counts.\n"
    )


def build_omni_resynthesis_messages(
    current_snapshot: dict[str, Any] | None,
    new_cards: list[dict[str, Any]],
    acted_cards: list[dict[str, Any]],
    pinned_items: list[dict[str, Any]],
    density: str = "compact",
    space_id: str = "default",
) -> list[dict[str, str]]:
    """Build messages for a full Omni resynthesis."""

    # Current snapshot
    if current_snapshot:
        snapshot_text = f"\n[CURRENT OMNI SNAPSHOT]\n{json.dumps(current_snapshot, indent=2)}\n[END CURRENT SNAPSHOT]"
    else:
        snapshot_text = "\n[CURRENT OMNI SNAPSHOT]\nEmpty — this is the first synthesis.\n[END CURRENT SNAPSHOT]"

    # New cards since last resynthesis
    cards_text = "\n[NEW CARDS SINCE LAST SYNTHESIS]\n"
    if new_cards:
        for card in new_cards:
            acted = " [USER ACTED]" if card.get("user_feedback") else ""
            cards_text += (
                f"- [{card.get('priority', 'MEDIUM')}] [{card.get('source_platform', '?')}] "
                f"{card.get('header', 'Untitled')} — {card.get('summary', '')}"
                f" (card_id: {card.get('card_id', '?')}){acted}\n"
            )
    else:
        cards_text += "No new cards.\n"
    cards_text += "[END NEW CARDS]\n"

    # User-acted cards (higher weight)
    acted_text = ""
    if acted_cards:
        acted_text = "\n[USER-ACTED CARDS — HIGHER WEIGHT]\n"
        for card in acted_cards:
            acted_text += (
                f"- [{card.get('status', '?')}] {card.get('header', 'Untitled')} "
                f"— feedback: {card.get('user_feedback', 'none')} "
                f"(card_id: {card.get('card_id', '?')})\n"
            )
        acted_text += "[END USER-ACTED CARDS]\n"

    # Pinned items
    pins_text = ""
    if pinned_items:
        pins_text = "\n[PINNED ITEMS — MUST PRESERVE EXACTLY]\n"
        for pin in pinned_items:
            pins_text += f"- {pin.get('item_text', '')} (platforms: {pin.get('platforms', [])})\n"
        pins_text += "[END PINNED ITEMS]\n"

    density_text = _density_instructions(density)

    user_message = (
        f"{current_timestamp_line()}\n\n"
        f"Synthesize the Omni summary for space '{space_id}'.\n"
        f"{snapshot_text}\n"
        f"{cards_text}"
        f"{acted_text}"
        f"{pins_text}"
        f"{density_text}\n"
        f"Produce the updated Omni sections JSON matching the required schema."
    )

    return [
        {"role": "system", "content": OMNI_RESYNTHESIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def get_omni_json_schema(density: str = "compact") -> dict[str, Any]:
    """Return the JSON schema for Omni resynthesis output.

    The ``density`` preset is used to set a hard ``maxItems`` cap on each
    section's items array, so the LLM cannot return more items than the
    density allows even if it ignores the prompt instruction.
    """
    preset = DENSITY_PRESETS.get(density, DENSITY_PRESETS["compact"])
    max_items = preset["max_items_per_section"]

    item_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Concise summary of this item",
            },
            "source_cards": {
                "type": "array",
                "items": {"type": "string"},
                "description": "card_ids that contributed to this item",
            },
            "platforms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Platforms involved (e.g., 'jira', 'bitbucket', 'gmail')",
            },
            "priority": {
                "type": "string",
                "description": "CRITICAL, HIGH, MEDIUM, or LOW",
            },
            "pinned": {
                "type": "boolean",
                "description": "True if this is a pinned item",
            },
        },
        "required": ["text", "source_cards", "platforms", "priority", "pinned"],
        "additionalProperties": False,
    }

    section_schema = {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "description": "Section type: attention, recent, period, or milestone",
            },
            "label": {
                "type": ["string", "null"],
                "description": "Optional label (e.g., 'Sprint 14 (Mar 25 – Apr 7)')",
            },
            "items": {
                "type": "array",
                "items": item_schema,
                "maxItems": max_items,
                "description": f"Items in this section (max {max_items})",
            },
        },
        "required": ["type", "label", "items"],
        "additionalProperties": False,
    }

    return {
        "name": "omni_snapshot",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "items": section_schema,
                    "description": "Exactly 4 sections: attention, recent, period, milestone",
                },
            },
            "required": ["sections"],
            "additionalProperties": False,
        },
    }
