# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Omni prompt templates — rolling cross-platform summary synthesis."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from laya.llm.prompts.overrides import get_prompt

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
2. [CURRENT ATTENTION]: the attention list as it stands, item by item with each \
item's live state — or an explicit statement that it is EMPTY. Either way you \
decide what attention looks like after this run: re-evaluate every current item \
against the exit rules, and every new card and prior aggregate against the admission \
rules.
3. New cards since the last synthesis (with metadata: platform, priority, user actions)
4. Pinned items that MUST survive compression
5. Density constraints (max items per section, max words per item)

## Section Types (produce exactly 4, in this order)

### attention
What needs the user NOW. Every line here is an open obligation of the user's — not \
news. Lines MAY be individual items because each is a specific actionable thing, but \
when several share a theme, aggregate them: "3 PRs awaiting your review (oldest: 5 days)." \
Build this section FIRST: walk the NEW CARDS and the PRIOR RECENT AGGREGATES and ask of \
each subject whether the user has to act on it. The admission and exit rules are in \
ATTENTION RULES below. `recent` is what happened that does NOT need the user.

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
Information flows: recent → period → milestone → gone. The attention section is NOT \
part of this chain: an attention item stays in attention, run after run, until its \
subject is resolved or de-escalated (see ATTENTION RULES below). Never fold an \
open attention item into recent or period just because time has passed, and never \
drop it to make room.

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
anywhere in your output. Use plain text only.

13. **Tag entity_ids on every item.** Each item's entity_ids array MUST list the \
entity_id of every contributing card (e.g., ["jira:ticket:PROJ-89", \
"github:pull_request:org/repo/#412"]). The new-card lines give you each card's \
entity_id. This is the stable identity Omni uses to drop a subject once it resolves \
— an item with no entity_ids cannot be reconciled later.

## ATTENTION RULES — what enters, what leaves

You receive two extra inputs that tell you what has changed state since the prior \
snapshot was written:

- [CURRENT STATE OF PRIOR SNAPSHOT ITEMS]: for each item already in the snapshot, \
whether all of its source subjects are now resolved, and the highest priority still \
live among them.
- [RESOLVED SINCE LAST SYNTHESIS]: subjects (by entity_id) that reached a terminal \
state (done / dismissed / archived / merged / closed) since the last synthesis.

Work subject by subject. A subject is identified by its entity_id(s). A new card whose \
entity_id matches a prior attention item is an UPDATE to that subject: decide what the \
update means for it (admission, exit, or neither) before writing the section.

### Admission — a subject ENTERS attention when it is still open and the USER has to act

Attention is an obligation list, not a priority bucket: the feed already sorts cards \
by priority. Priority is an INPUT to this judgement — HIGH/CRITICAL says the router \
found it urgent, so look hard at it — but it is not the test. A CRITICAL incident the \
user merely observes is recent news; a MEDIUM question addressed to the user is \
attention. Admit a subject when any of these holds:

A1. **Asked of the user.** The user is the reviewer, approver or assignee; the user is \
addressed by name or asked a direct question; a decision, sign-off, RSVP or reply is \
requested of the user.
A2. **The user is the bottleneck.** Someone is waiting on the user: an unreviewed PR \
assigned to them, an unanswered thread directed at them, a teammate blocked on them.
A3. **Broken on the user's own subject.** A build, CI run, deploy or check failed on \
the user's PR, branch or service; an incident, outage or security alert is in the \
user's area and nobody else is named as handling it.
A4. **Overdue or ageing.** A due date passed or is imminent, a reminder says overdue, \
or a request to the user has gone unanswered long enough to say so ("oldest: 5 days").
A5. **Escalation of a known subject.** A subject already in recent or period receives \
a new card that adds a direct ask, names the user, or breaks something of theirs — it \
moves UP into attention and out of the section it was in.
A6. **Pinned.** A pinned item that is still open stays in attention as written.

The card summaries already carry the user's relationship to each subject (reviewer, \
author, assignee, mentioned); read them for A1–A3 rather than guessing. When in doubt \
between attention and recent, ask: if the user did nothing, would someone be waiting?

### Exit — a subject LEAVES attention only for one of these reasons

E1. **Resolved.** Its state line says ALL source subjects RESOLVED, or its entity_id is \
in [RESOLVED SINCE LAST SYNTHESIS]: PR merged or declined, ticket closed, card marked \
done or dismissed.
E2. **Superseded on the same subject.** A newer card on the SAME entity shows the ask \
was met: "build passed" after "build failed", "approved" or "review submitted" after \
"review requested", a reply after a question, "assigned to X" after "assigned to you", \
"rescheduled" or "cancelled" after a meeting request.
E3. **Made obsolete by another subject.** A different subject closes this one \
transitively: the PR was closed in favour of a replacement, the ticket was marked \
duplicate of or merged into another, a release or hotfix shipped the fix, the incident \
was declared over, the requester withdrew.
E4. **Handed off.** Someone else took the review, the assignment or the on-call; the \
user is no longer the party needed.
E5. **User acted.** A [USER ACTED] card shows the user approved, replied, dismissed \
with feedback, or otherwise handled it.
E6. **Expired.** The moment passed and nothing can be done now: the meeting happened, \
the deadline lapsed and the task was reassigned, the window closed.
E7. **No longer an obligation.** On re-reading, none of A1–A6 holds any more — the \
ask was informational after all, or the state line shows the priority fell to LOW.

Several attention lines about the same subject merge into one line — that is \
consolidation, not an exit.

### What to do with an exit

- Drop the subject from attention, or rebuild its aggregate without it and decrement \
the count ("3 PRs awaiting your review" → "2 PRs awaiting your review"). If every \
member of an aggregate left, drop the whole line.
- Record EVERY exit in the top-level `attention_exits` array: the subject's \
entity_ids, a reason code (resolved | superseded | obsolete | handed_off | user_acted \
| expired | deprioritised) and a one-clause note citing the evidence ("build passed on \
PR-748 at 09:12"). The changelog shows the user why each line left; a line that \
disappears without an entry shows as "compressed away", which is wrong for an \
obligation.
- Reflect completion as progress, not silence: resolved and superseded work surfaces \
in recent or period as a positive aggregate ("3 blockers cleared this week, incl. \
PR #412 merged and PROJ-89 closed").

Nothing else removes a subject from attention: not age alone, not the density cap, \
not the compression chain. If attention is over the cap, keep the highest priorities \
and fold the rest into ONE aggregate line — never drop them."""


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
    item_states: list[dict[str, Any]] | None = None,
    resolved_cards: list[dict[str, Any]] | None = None,
    prior_recent_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build messages for a full Omni resynthesis.

    item_states: per-item live state of the CURRENT snapshot, so the LLM can drop
        resolved/de-escalated subjects. Each entry: {text, all_resolved,
        live_max_priority}.
    resolved_cards: subjects that reached a terminal state since the last synthesis.
        Each entry: {entity_id, header, status}.
    prior_recent_items: when given (the FIRST fold of a run), the snapshot's
        ``recent`` section is presented EMPTY and these items are listed in a
        separate block: those the user still has to act on are attention
        material, the rest period/milestone material. A small model handed a ``recent``
        list that is already at the density cap tends to re-emit it verbatim and
        discard the new cards, so the structure of the input makes "rebuild
        recent from the new cards" the only shape that fits the schema. Later
        folds of the same run pass ``None`` — their ``recent`` was built from
        this run's own cards and must be merged into, not stripped.
    """

    # Current snapshot. The caller's dict is left untouched: the pipeline computes
    # its stats and prune hints from the same object after this call.
    if current_snapshot:
        shown = current_snapshot
        if prior_recent_items is not None:
            shown = {
                **current_snapshot,
                "sections": [
                    {**s, "items": []} if s.get("type") == "recent" else s
                    for s in current_snapshot.get("sections", [])
                ],
            }
        snapshot_text = f"\n[CURRENT OMNI SNAPSHOT]\n{json.dumps(shown, indent=2)}\n[END CURRENT SNAPSHOT]"
    else:
        snapshot_text = "\n[CURRENT OMNI SNAPSHOT]\nEmpty — this is the first synthesis.\n[END CURRENT SNAPSHOT]"

    # The attention list, stated explicitly. The snapshot JSON above carries the
    # same items, but an empty section there is just `"items": []` inside a
    # large object, and a model reads that as "nothing to do" rather than as
    # "decide what belongs here". Each current item is paired with its live
    # state so the exit decision and the item are read together.
    state_by_text: dict[str, dict] = {
        (st.get("text") or "")[:80]: st for st in (item_states or [])
    }
    attention_items = [
        item
        for s_ in (current_snapshot or {}).get("sections", [])
        if s_.get("type") == "attention"
        for item in s_.get("items", [])
    ]
    attention_text = "\n[CURRENT ATTENTION]\n"
    if attention_items:
        attention_text += (
            f"{len(attention_items)} item(s) currently need the user. Re-evaluate each "
            "against the exit rules E1–E7 using the new cards and its live state; keep "
            "it unless an exit applies, and record every exit in attention_exits.\n"
        )
        for item in attention_items:
            st = state_by_text.get((item.get("text") or "")[:80])
            if st is None:
                live = "live state unknown"
            elif st.get("all_resolved"):
                live = "ALL source subjects RESOLVED"
            else:
                live = f"highest live priority now {st.get('live_max_priority') or 'none'}"
            attention_text += json.dumps({
                "text": item.get("text", ""),
                "priority": item.get("priority", "MEDIUM"),
                "entity_ids": sorted({
                    e for e in (item.get("entity_ids") or []) if e
                } | ({item["entity_id"]} if item.get("entity_id") else set())),
                "source_cards": list(item.get("source_cards") or []),
                "live": live,
            }) + "\n"
    else:
        attention_text += (
            "EMPTY — nothing is currently flagged as needing the user. That is not a "
            "default to keep: walk every NEW CARD and every PRIOR RECENT AGGREGATE "
            "against the admission rules A1–A6 and decide whether any subject must now "
            "be added. Leave attention empty only if genuinely nothing needs the user.\n"
        )
    attention_text += "[END CURRENT ATTENTION]\n"

    # The previous run's recent aggregates. One compact JSON object per line so
    # the model carries each aggregate's source_cards/entity_ids into whichever
    # section it lands in — an aggregate without ids has no drill-down and
    # cannot be reconciled when its subject resolves. (A `[PRIORITY] text` line
    # format here ends up copied verbatim, bracket and all, into output text.)
    prior_recent_text = ""
    if prior_recent_items:
        prior_recent_text = (
            "\n[PRIOR RECENT AGGREGATES — last run's recent: promote to attention if "
            "the user still has to act on it (ATTENTION RULES A1–A6), otherwise fold "
            "into period/milestone; never copy into recent]\n"
        )
        for item in prior_recent_items:
            prior_recent_text += json.dumps({
                "text": item.get("text", ""),
                "priority": item.get("priority", "MEDIUM"),
                "platforms": list(item.get("platforms") or []),
                "source_cards": list(item.get("source_cards") or []),
                "entity_ids": sorted({
                    e for e in (item.get("entity_ids") or []) if e
                } | ({item["entity_id"]} if item.get("entity_id") else set())),
            }) + "\n"
        prior_recent_text += "[END PRIOR RECENT AGGREGATES]\n"

    # New cards since last resynthesis
    cards_text = "\n[NEW CARDS SINCE LAST SYNTHESIS]\n"
    if new_cards:
        for card in new_cards:
            acted = " [USER ACTED]" if card.get("user_feedback") else ""
            tags_str = f" [tags: {card['tags']}]" if card.get("tags") else ""
            entity_str = f" [entity: {card['entity_id']}]" if card.get("entity_id") else ""
            cards_text += (
                f"- [{card.get('priority', 'MEDIUM')}] [{card.get('source_platform', '?')}] "
                f"{card.get('header', 'Untitled')} — {card.get('summary', '')}"
                f" (card_id: {card.get('card_id', '?')}){entity_str}{acted}{tags_str}\n"
            )
    else:
        cards_text += "No new cards.\n"
    cards_text += "[END NEW CARDS]\n"

    # Per-item live state of the current snapshot — lets the LLM drop resolved /
    # de-escalated subjects instead of carrying them forward forever.
    state_text = ""
    if item_states:
        state_text = "\n[CURRENT STATE OF PRIOR SNAPSHOT ITEMS]\n"
        for st in item_states:
            snippet = (st.get("text") or "")[:80]
            if st.get("all_resolved"):
                state_text += f"- \"{snippet}\" -> ALL source subjects RESOLVED (drop from attention)\n"
            else:
                lmp = st.get("live_max_priority") or "none"
                state_text += f"- \"{snippet}\" -> highest live priority now {lmp}\n"
        state_text += "[END CURRENT STATE]\n"

    # Subjects that reached a terminal state since the last synthesis.
    resolved_text = ""
    if resolved_cards:
        resolved_text = "\n[RESOLVED SINCE LAST SYNTHESIS]\n"
        for rc in resolved_cards:
            resolved_text += (
                f"- {rc.get('entity_id', '?')} — {rc.get('header', 'Untitled')} "
                f"(resolved: {rc.get('status', 'done')})\n"
            )
        resolved_text += "[END RESOLVED]\n"

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

    # Structural rules repeated in the user turn so a custom system-prompt
    # override (prompts/overrides.py) cannot drop them.
    structure_text = ""
    if prior_recent_items is not None:
        structure_text = (
            "\nSTRUCTURE RULES FOR THIS RUN:\n"
            "- `attention` first: every still-open subject the user has to act on "
            "(ATTENTION RULES A1–A6), from the NEW CARDS and from the PRIOR RECENT "
            "AGGREGATES, grouped by theme. Priority informs this, it does not decide it.\n"
            "- Then `recent`, from the remaining NEW CARDS only. Prior recent aggregates "
            "that are not attention go to period/milestone, keeping their source_cards "
            "and entity_ids.\n"
            "- A prior attention item stays in `attention` unless an exit rule (E1–E7) "
            "applies; record every exit in `attention_exits` with its reason and "
            "evidence.\n"
        )

    user_message = (
        f"Synthesize the Omni summary for space '{space_id}'.\n"
        f"{snapshot_text}\n"
        f"{attention_text}"
        f"{prior_recent_text}"
        f"{state_text}"
        f"{resolved_text}"
        f"{cards_text}"
        f"{acted_text}"
        f"{pins_text}"
        f"{density_text}"
        f"{structure_text}\n"
        f"Produce the updated Omni sections JSON matching the required schema."
    )

    return [
        {"role": "system", "content": get_prompt("omni", OMNI_RESYNTHESIS_SYSTEM_PROMPT)},
        {"role": "user", "content": user_message},
    ]


def build_omni_repair_messages(
    messages: list[dict[str, str]],
    assistant_json: str,
    new_card_count: int,
) -> list[dict[str, str]]:
    """Extend a resynthesis conversation with one repair turn.

    Used when the model's output cites none of the new cards: rather than
    discarding a summary that may be right in substance, the model is shown its
    own output and asked to finish the job — attach the card_ids each line
    covers, or fold the cards it skipped. The decision stays with the model;
    the pipeline only points out what is missing.
    """
    repair = (
        f"Your output cites none of the {new_card_count} new cards listed in "
        "[NEW CARDS SINCE LAST SYNTHESIS]. Every line's source_cards must name the "
        "card_ids it summarises, and every new card must be accounted for by some "
        "line (rule 2 — use a catch-all aggregate if nothing else fits). If what you "
        "returned is last run's aggregates unchanged, that is not a synthesis: fold "
        "the new cards in.\n\n"
        "Re-issue the COMPLETE JSON — all four sections and attention_exits — "
        "attaching card_ids to the lines that cover these cards and folding any card "
        "you have not covered into an aggregate line. Keep everything else as it was."
    )
    return [
        *messages,
        {"role": "assistant", "content": assistant_json},
        {"role": "user", "content": repair},
    ]


def get_omni_json_schema(
    density: str = "compact",
    valid_card_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return the JSON schema for Omni resynthesis output.

    The ``density`` preset is used to set a hard ``maxItems`` cap on each
    section's items array, so the LLM cannot return more items than the
    density allows even if it ignores the prompt instruction.

    ``valid_card_ids`` — the cards the model was actually given this run (the
    new cards plus those behind the prior snapshot's items). When provided,
    every item's ``source_cards`` must name at least one of them and nothing
    else: structured-output backends enforce this as a grammar, so the model
    cannot emit an item with no drill-down or cite a card it never saw. Rule 7
    of the prompt asks for this; the schema makes it unavoidable.
    """
    preset = DENSITY_PRESETS.get(density, DENSITY_PRESETS["compact"])
    max_items = preset["max_items_per_section"]

    source_cards_schema: dict[str, Any] = {
        "type": "array",
        "items": {"type": "string"},
        "description": "card_ids that contributed to this item",
    }
    ids = sorted({c for c in (valid_card_ids or []) if c})
    if ids:
        source_cards_schema["items"] = {"type": "string", "enum": ids}
        source_cards_schema["minItems"] = 1
        source_cards_schema["description"] = (
            "card_ids that contributed to this item — at least one, and only "
            "ids from the cards you were given"
        )

    item_schema = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Concise summary of this item",
            },
            "source_cards": source_cards_schema,
            "entity_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "entity_ids of ALL contributing cards (the stable subject "
                    "identity, e.g. 'jira:ticket:PROJ-89'). Used to correlate "
                    "resolved/de-escalated subjects across resyntheses."
                ),
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
        "required": [
            "text", "source_cards", "entity_ids", "platforms", "priority", "pinned",
        ],
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
                "anyOf": [{"type": "string"}, {"type": "null"}],
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
                "attention_exits": {
                    "type": "array",
                    "description": (
                        "Every subject that left attention this run, with why. "
                        "Empty array when nothing left."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "entity_ids of the subject that left attention",
                            },
                            "reason": {
                                "type": "string",
                                "description": (
                                    "resolved | superseded | obsolete | handed_off | "
                                    "user_acted | expired | deprioritised"
                                ),
                            },
                            "note": {
                                "type": "string",
                                "description": "One clause citing the evidence",
                            },
                        },
                        "required": ["entity_ids", "reason", "note"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["sections", "attention_exits"],
            "additionalProperties": False,
        },
    }
