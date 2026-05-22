# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the INGEST pipeline step."""

import json

import pytest

from laya.pipeline.ingest import resolve_actor_relationship, run_ingest


@pytest.mark.asyncio
async def test_resolve_known_teammate(sample_event, mock_team):
    role = await resolve_actor_relationship(sample_event)
    assert role == "teammate"


@pytest.mark.asyncio
async def test_resolve_known_manager(sample_event, mock_team):
    sample_event.actor.email = "mike@company.com"
    role = await resolve_actor_relationship(sample_event)
    assert role == "manager"


@pytest.mark.asyncio
async def test_resolve_known_bot(sample_event, mock_team):
    sample_event.actor.email = "ci@company.com"
    role = await resolve_actor_relationship(sample_event)
    assert role == "bot"


@pytest.mark.asyncio
async def test_resolve_unknown_defaults_external(sample_event, mock_team):
    sample_event.actor.email = "stranger@other.com"
    sample_event.actor.name = "Unknown Person"
    role = await resolve_actor_relationship(sample_event)
    assert role == "external"


@pytest.mark.asyncio
async def test_resolve_name_fallback_when_no_email(sample_event, mock_team):
    """Platforms like Bitbucket/GitHub send empty email — fall back to name matching."""
    sample_event.actor.email = ""
    sample_event.actor.name = "Sarah Chen"
    role = await resolve_actor_relationship(sample_event)
    assert role == "teammate"


@pytest.mark.asyncio
async def test_resolve_name_fallback_case_insensitive(sample_event, mock_team):
    sample_event.actor.email = ""
    sample_event.actor.name = "sarah chen"
    role = await resolve_actor_relationship(sample_event)
    assert role == "teammate"


@pytest.mark.asyncio
async def test_resolve_case_insensitive(sample_event, mock_team):
    sample_event.actor.email = "Sarah@Company.COM"
    role = await resolve_actor_relationship(sample_event)
    assert role == "teammate"


@pytest.mark.asyncio
async def test_run_ingest_updates_db(db, sample_event, mock_team):
    # Insert event row first
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sample_event.event_id, "2026-02-22", "jira", "issue_assigned", "ticket", "BUG-1234", "{}"),
    )
    await db.commit()

    role, participant_roles = await run_ingest(sample_event)
    assert role == "teammate"
    assert participant_roles["laya_user_role"] is None
    assert participant_roles["actor_role"] is None
    assert participant_roles["participants"] == []

    async with db.execute(
        "SELECT actor_relationship FROM events WHERE event_id=?", (sample_event.event_id,)
    ) as cursor:
        row = await cursor.fetchone()
        assert row[0] == "teammate"
