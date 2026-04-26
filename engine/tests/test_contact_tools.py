"""Tests for the find_contact tool."""

import pytest
import pytest_asyncio
from unittest.mock import patch

from tests.conftest import insert_test_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEAM_WITH_ALIASES = {
    "members": [
        {
            "name": "John Schumer",
            "email": "john@work.com",
            "role": "teammate",
            "notes": "Backend engineer",
            "aliases": ["john.schumer@work.com", "jschumer@work.com"],
            "accounts": ["johns", "jschumer"],
        },
        {
            "name": "Sarah Chen",
            "email": "sarah@company.com",
            "role": "manager",
            "notes": "EM",
            "aliases": [],
            "accounts": ["schen"],
        },
        {
            "name": "CI Bot",
            "email": "ci@company.com",
            "role": "bot",
            "notes": "",
            "aliases": [],
            "accounts": [],
        },
        {
            "name": "Susie Park",
            "email": "susie@company.com",
            "role": "teammate",
            "notes": "Frontend",
            "aliases": [],
            "accounts": ["susiepark"],
        },
    ]
}

EMPTY_TEAM = {"members": []}


async def _insert_contact_events(db):
    """Seed the events table with various actors."""
    await insert_test_event(
        db, event_id="evt_j1", platform="jira",
        actor_name="John Schumer", actor_email="john.schumer@work.com",
    )
    await insert_test_event(
        db, event_id="evt_j2", platform="gmail",
        actor_name="John S.", actor_email="john.schumer@work.com",
    )
    await insert_test_event(
        db, event_id="evt_s1", platform="slack",
        actor_name="Sarah Chen", actor_email="sarah@company.com",
    )
    await insert_test_event(
        db, event_id="evt_s2", platform="jira",
        actor_name="Sarah Chen", actor_email="sarah@company.com",
    )
    await insert_test_event(
        db, event_id="evt_m1", platform="bitbucket",
        actor_name="Mike Torres", actor_email="mike@elsewhere.com",
    )
    await insert_test_event(
        db, event_id="evt_su1", platform="slack",
        actor_name="Susie Park", actor_email="susie@company.com",
    )
    # Event with actor_handle set via direct SQL (insert_test_event doesn't support it)
    await db.execute(
        """INSERT INTO events
           (event_id, timestamp, source_platform, source_raw_event_type,
            subject_type, subject_id, subject_title, actor_name, actor_email,
            actor_handle, content_body, raw_json, processed, filtered)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("evt_h1", "2026-02-22T14:30:00Z", "github", "pr_opened",
         "pr", "PR-1", "Fix bug", "JSchumer", "john.schumer@work.com",
         "jschumer", "body", "{}", True, False),
    )
    await db.commit()


def _hits(result: dict, query: str) -> list[dict]:
    """Extract the contact list for a given query from a find_contact result."""
    return result[query]


# ---------------------------------------------------------------------------
# Single-query tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exact_match_by_name(db):
    """Exact name match returns the right contact."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("Sarah Chen")

    contacts = _hits(result, "Sarah Chen")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "sarah@company.com"
    assert "Sarah Chen" in contacts[0]["names"]
    assert contacts[0]["match_type"] == "exact"


@pytest.mark.asyncio
async def test_exact_match_by_email(db):
    """Exact email match works."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("mike@elsewhere.com")

    contacts = _hits(result, "mike@elsewhere.com")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "mike@elsewhere.com"
    assert "Mike Torres" in contacts[0]["names"]


@pytest.mark.asyncio
async def test_exact_match_by_handle(db):
    """Exact handle match works (with and without @ prefix)."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("@jschumer")

    contacts = _hits(result, "@jschumer")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "john.schumer@work.com"


@pytest.mark.asyncio
async def test_dedup_same_email_different_names(db):
    """Same email across events produces one contact with all names."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("john.schumer@work.com")

    contacts = _hits(result, "john.schumer@work.com")
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["email"] == "john.schumer@work.com"
    assert "John Schumer" in contact["names"]
    assert "John S." in contact["names"]
    assert "jira" in contact["platforms"]
    assert "gmail" in contact["platforms"]


@pytest.mark.asyncio
async def test_fuzzy_match_fallback(db):
    """Fuzzy match kicks in when no exact match exists."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("John")

    contacts = _hits(result, "John")
    assert len(contacts) >= 1
    emails = [r["email"] for r in contacts]
    assert "john.schumer@work.com" in emails
    assert all(r["match_type"] == "fuzzy" for r in contacts)


@pytest.mark.asyncio
async def test_fuzzy_returns_multiple_matches(db):
    """Fuzzy match on a common substring returns multiple contacts."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("company.com")

    contacts = _hits(result, "company.com")
    assert len(contacts) >= 1
    emails = [r["email"] for r in contacts]
    assert "sarah@company.com" in emails


@pytest.mark.asyncio
async def test_team_enrichment(db):
    """Team.json data enriches event-sourced contacts."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("sarah@company.com")

    contacts = _hits(result, "sarah@company.com")
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["relationship"] == "manager"
    assert "schen" in contact["handles"]


@pytest.mark.asyncio
async def test_team_alias_merges_under_primary(db):
    """Events with a team alias email merge into the primary email bucket."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("john.schumer@work.com")

    contacts = _hits(result, "john.schumer@work.com")
    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["email"] == "john@work.com"
    assert "John Schumer" in contact["names"]
    assert "John S." in contact["names"]
    assert contact["relationship"] == "teammate"
    assert "johns" in contact["handles"] or "jschumer" in contact["handles"]


@pytest.mark.asyncio
async def test_team_only_match_no_events(db):
    """A team member with no events is still findable."""
    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("CI Bot")

    contacts = _hits(result, "CI Bot")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "ci@company.com"
    assert contacts[0]["relationship"] == "bot"
    assert "CI Bot" in contacts[0]["names"]


@pytest.mark.asyncio
async def test_team_account_match(db):
    """Searching by a platform account/handle from team.json works."""
    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("schen")

    contacts = _hits(result, "schen")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "sarah@company.com"


@pytest.mark.asyncio
async def test_no_results(db):
    """Non-matching query returns empty list for that key."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("zzz_nonexistent_zzz")

    assert _hits(result, "zzz_nonexistent_zzz") == []


@pytest.mark.asyncio
async def test_empty_query(db):
    """Empty/whitespace query returns empty dict."""
    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        assert await find_contact("") == {}
        assert await find_contact("   ") == {}


@pytest.mark.asyncio
async def test_case_insensitive(db):
    """Search is case-insensitive."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("SARAH CHEN")

    contacts = _hits(result, "SARAH CHEN")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "sarah@company.com"


@pytest.mark.asyncio
async def test_handles_collected(db):
    """Handles from events are collected in the contact."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("john.schumer@work.com")

    contact = _hits(result, "john.schumer@work.com")[0]
    assert "jschumer" in contact["handles"]


@pytest.mark.asyncio
async def test_exact_preferred_over_fuzzy(db):
    """When exact match exists, fuzzy is not run and match_type is 'exact'."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("Sarah Chen")

    contacts = _hits(result, "Sarah Chen")
    assert all(r["match_type"] == "exact" for r in contacts)


@pytest.mark.asyncio
async def test_platforms_collected(db):
    """All platforms for a contact are collected."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("sarah@company.com")

    contact = _hits(result, "sarah@company.com")[0]
    assert "slack" in contact["platforms"]
    assert "jira" in contact["platforms"]


# ---------------------------------------------------------------------------
# Batch-query tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_multiple_queries(db):
    """Batch lookup returns results keyed by each query."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact(["John Schumer", "Sarah Chen"])

    assert "John Schumer" in result
    assert "Sarah Chen" in result
    assert len(result) == 2

    john_contacts = result["John Schumer"]
    assert len(john_contacts) >= 1
    assert any(c["email"] == "john@work.com" for c in john_contacts)

    sarah_contacts = result["Sarah Chen"]
    assert len(sarah_contacts) == 1
    assert sarah_contacts[0]["email"] == "sarah@company.com"


@pytest.mark.asyncio
async def test_batch_mixed_hit_and_miss(db):
    """Batch with one matching and one non-matching query."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact(["Sarah Chen", "Nobody Real"])

    assert len(result["Sarah Chen"]) == 1
    assert result["Nobody Real"] == []


@pytest.mark.asyncio
async def test_batch_empty_strings_filtered(db):
    """Blank entries in a batch are silently dropped."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact(["Sarah Chen", "", "  "])

    assert len(result) == 1
    assert "Sarah Chen" in result


@pytest.mark.asyncio
async def test_batch_empty_list(db):
    """Empty list returns empty dict."""
    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        assert await find_contact([]) == {}


@pytest.mark.asyncio
async def test_batch_team_loaded_once(db):
    """Team.json is loaded once for the whole batch, not per query."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES) as mock_load:
        from laya.llm.tools.contact_tools import find_contact
        await find_contact(["John Schumer", "Sarah Chen", "Susie Park"])

    mock_load.assert_called_once()


@pytest.mark.asyncio
async def test_batch_independent_results(db):
    """Each query in a batch resolves independently."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=TEAM_WITH_ALIASES):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact(["schen", "CI Bot", "mike@elsewhere.com"])

    assert result["schen"][0]["email"] == "sarah@company.com"
    assert result["CI Bot"][0]["email"] == "ci@company.com"
    assert result["mike@elsewhere.com"][0]["email"] == "mike@elsewhere.com"


@pytest.mark.asyncio
async def test_single_string_still_returns_dict(db):
    """A single string input returns a dict keyed by that string."""
    await _insert_contact_events(db)

    with patch("laya.llm.tools.contact_tools.load_team", return_value=EMPTY_TEAM):
        from laya.llm.tools.contact_tools import find_contact
        result = await find_contact("Sarah Chen")

    assert isinstance(result, dict)
    assert "Sarah Chen" in result
