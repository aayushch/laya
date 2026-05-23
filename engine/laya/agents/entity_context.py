# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Entity-level context builder for agent sessions.

Builds a CONTEXT.md file containing group summary, individual card details,
and any engineer task prompts — giving the agent full context for the entity.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import structlog

from laya.config import LAYA_HOME
from laya.db.sqlite import get_db

log = structlog.get_logger()

_UNSAFE_FS_CHARS = re.compile(r'[:/\\<>"|?*]')


def sanitize_entity_id(entity_id: str) -> str:
    """Replace filesystem-unsafe characters in entity_id with underscores."""
    return _UNSAFE_FS_CHARS.sub("_", entity_id)


def get_entity_research_dir(entity_id: str) -> Path:
    """Return the research directory for an entity, creating it if needed."""
    dir_path = LAYA_HOME / "tmp" / "research" / sanitize_entity_id(entity_id)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


async def build_entity_context_markdown(
    entity_id: str,
    space_id: str | None = None,
) -> str:
    """Build the full CONTEXT.md content for an entity group.

    Queries group_summaries, action_cards, and events to compose a comprehensive
    markdown document with all entity context.
    """
    db = await get_db()

    # Fetch all cards for this entity
    card_rows = await db.execute_fetchall(
        """SELECT ac.card_id, ac.created_at, ac.priority, ac.persona, ac.category,
                  ac.header, ac.summary, ac.intelligence, ac.status, ac.agent_prompt,
                  ac.source_ref, ac.source_url,
                  e.content_body, e.source_platform
           FROM action_cards ac
           LEFT JOIN events e ON e.event_id = ac.event_id
           WHERE ac.entity_id = ?
           ORDER BY ac.created_at DESC""",
        (entity_id,),
    )

    if not card_rows:
        return f"# Entity Context\nEntity ID: {entity_id}\n\nNo cards found for this entity.\n"

    # Determine entity title from the first card's header
    entity_title = card_rows[0]["header"] or entity_id

    # Fetch group summary if available
    summary_rows = await db.execute_fetchall(
        "SELECT headline, summary, key_events, current_status, pending_actions FROM group_summaries WHERE entity_id = ?",
        (entity_id,),
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# Entity Context: {entity_title}",
        f"Entity ID: {entity_id}",
        f"Generated: {now}",
        "",
    ]

    # Group summary section
    if summary_rows:
        s = summary_rows[0]
        lines.append("## Group Summary")
        lines.append(f"**{s['headline']}**")
        lines.append("")
        lines.append(s["summary"])
        lines.append("")

        key_events = json.loads(s["key_events"]) if s["key_events"] else []
        if key_events:
            lines.append("### Key Developments")
            for ev in key_events:
                lines.append(f"- {ev}")
            lines.append("")

        if s["current_status"]:
            lines.append("### Current Status")
            lines.append(s["current_status"])
            lines.append("")

        pending = json.loads(s["pending_actions"]) if s["pending_actions"] else []
        if pending:
            lines.append("### Pending Actions")
            for action in pending:
                lines.append(f"- {action}")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Cards section
    lines.append(f"## Cards ({len(card_rows)} total)")
    lines.append("")

    latest_engineer_prompt = None
    latest_engineer_card_id = None

    for row in card_rows:
        card_id = row["card_id"]
        lines.append(f"### Card: {card_id} ({row['persona']} / {row['status']}) — {row['created_at'] or 'unknown'}")
        lines.append(f"**{row['header']}**")
        lines.append("")
        if row["summary"]:
            lines.append(row["summary"])
            lines.append("")

        # Intelligence / key points
        intel = []
        if row["intelligence"]:
            try:
                parsed = json.loads(row["intelligence"])
                if isinstance(parsed, list):
                    intel = parsed
            except json.JSONDecodeError:
                pass
        if intel:
            lines.append("#### Key Points")
            for point in intel:
                lines.append(f"- {point}")
            lines.append("")

        # Source content (truncated)
        if row["content_body"]:
            body = row["content_body"][:2000]
            if len(row["content_body"]) > 2000:
                body += "\n... (truncated)"
            lines.append("#### Source Content")
            lines.append(f"Platform: {row['source_platform'] or 'unknown'}")
            lines.append(body)
            lines.append("")

        lines.append("---")
        lines.append("")

        # Track the latest engineer prompt
        if row["agent_prompt"] and row["persona"] == "ENGINEER" and latest_engineer_prompt is None:
            latest_engineer_prompt = row["agent_prompt"]
            latest_engineer_card_id = card_id

    # Engineer task prompt section
    if latest_engineer_prompt:
        lines.append("## Engineer Task Prompt")
        lines.append(f"(from card {latest_engineer_card_id})")
        lines.append("")
        lines.append(latest_engineer_prompt)
        lines.append("")

    return "\n".join(lines)


async def write_entity_context_file(
    entity_id: str,
    space_id: str | None = None,
) -> Path:
    """Build and write the CONTEXT.md file for an entity.

    Returns the path to the written file. Overwrites any existing file
    so that resumed agents get fresh context.
    """
    research_dir = get_entity_research_dir(entity_id)
    content = await build_entity_context_markdown(entity_id, space_id)
    context_path = research_dir / "CONTEXT.md"
    context_path.write_text(content, encoding="utf-8")
    log.info("entity_context_written", entity_id=entity_id, path=str(context_path))
    return context_path


async def refresh_entity_context_if_exists(
    entity_id: str,
    space_id: str | None = None,
) -> bool:
    """Refresh CONTEXT.md only if it already exists for this entity.

    Used by the post-emit pipeline so that when a new card arrives for an
    entity that already has a workspace (CONTEXT.md was previously written
    by run-agent), the agent gets up-to-date info on its next resume
    without requiring the user to re-explain what changed.

    Returns True if the file was refreshed, False if it didn't exist.
    """
    context_path = LAYA_HOME / "tmp" / "research" / sanitize_entity_id(entity_id) / "CONTEXT.md"
    if not context_path.exists():
        return False
    await write_entity_context_file(entity_id, space_id)
    return True


def build_entity_agent_prompt(
    entity_id: str,
    research_dir: str,
    repo_path: str | None = None,
    user_prompt: str | None = None,
) -> str:
    """Build the prompt for an entity-level agent session.

    Includes instructions about CONTEXT.md and the working environment.
    """
    lines = [
        f'You are a research and coding agent working on entity "{entity_id}" for Laya, '
        "an AI-powered professional work assistant.",
        "",
        f"A CONTEXT.md file is available at {research_dir}/CONTEXT.md with the full context "
        "for this entity: group summary, individual card details, and any coding task "
        "prompts prepared by Laya's ENGINEER worker.",
        "",
        "IMPORTANT: At the start of each turn, re-read CONTEXT.md — it may have been "
        "refreshed with new cards or updated summaries since your last turn.",
        "",
    ]

    if repo_path and repo_path != research_dir:
        lines.append(
            "The code repository is your working directory. "
            "Research context is available in the additional directory."
        )
    else:
        lines.append(
            "Your working directory is the research folder. "
            "Code repositories have been added as additional directories for reference."
        )

    lines.append("")
    lines.append("Never use emoji or icon characters in your output.")

    if user_prompt:
        lines.append("")
        lines.append("## Task")
        lines.append(user_prompt)

    return "\n".join(lines)
