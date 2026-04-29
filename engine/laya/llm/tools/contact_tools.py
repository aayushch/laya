"""Contact lookup tool — finds contacts by name, email, or handle."""

from __future__ import annotations

import structlog

from laya.config import load_team
from laya.db.sqlite import get_db
from laya.llm.tools.constants import CONTACT_SEARCH_MAX

log = structlog.get_logger()


async def find_contact(query: str | list[str]) -> dict[str, list[dict]]:
    """Find contacts matching one or more names, emails, or handles.

    Accepts a single query string or a list of strings for batch lookup.
    Searches the events table (all actors Laya has seen) and team.json
    (curated team members). Deduplicates by email address.

    Returns a dict keyed by each query string, with a list of matching
    contacts per query. Exact match first; falls back to LIKE-based fuzzy.
    """
    queries = [query] if isinstance(query, str) else list(query)
    queries = [q.strip() for q in queries if q.strip()]

    if not queries:
        return {}

    # Load shared context once for the whole batch
    team_members = load_team().get("members", [])
    alias_map = _build_alias_map(team_members)

    results: dict[str, list[dict]] = {}
    for q in queries:
        results[q] = await _find_single(q, team_members, alias_map)

    return results


def _build_alias_map(team_members: list[dict]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for member in team_members:
        primary = member["email"].lower()
        alias_map[primary] = primary
        for alias in member.get("aliases", []):
            alias_map[alias.lower()] = primary
    return alias_map


async def _find_single(
    query: str,
    team_members: list[dict],
    alias_map: dict[str, str],
) -> list[dict]:
    query_lower = query.lower()
    query_clean = query_lower.lstrip("@")

    # Phase 1: exact match
    event_rows = await _search_events(query_lower, query_clean, exact=True)
    team_hits = _search_team(query_lower, query_clean, team_members, exact=True)
    merged = _merge_and_dedup(event_rows, team_hits, team_members, alias_map, "exact")

    if merged:
        return merged

    # Phase 2: fuzzy (LIKE) match
    event_rows = await _search_events(query_lower, query_clean, exact=False)
    team_hits = _search_team(query_lower, query_clean, team_members, exact=False)
    return _merge_and_dedup(event_rows, team_hits, team_members, alias_map, "fuzzy")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _search_events(
    query_lower: str, query_clean: str, *, exact: bool
) -> list[dict]:
    db = await get_db()

    if exact:
        sql = """
            SELECT actor_email, actor_name, actor_handle,
                   actor_relationship, source_platform
            FROM events
            WHERE actor_email IS NOT NULL
              AND (LOWER(actor_email) = ?
                   OR LOWER(actor_name) = ?
                   OR LOWER(actor_handle) = ?)
        """
        params: tuple = (query_lower, query_lower, query_clean)
    else:
        escaped = _escape_like(query_clean)
        pattern = f"%{escaped}%"
        sql = """
            SELECT actor_email, actor_name, actor_handle,
                   actor_relationship, source_platform
            FROM events
            WHERE actor_email IS NOT NULL
              AND (LOWER(actor_name) LIKE ? ESCAPE '\\'
                   OR LOWER(actor_email) LIKE ? ESCAPE '\\'
                   OR LOWER(actor_handle) LIKE ? ESCAPE '\\')
        """
        params = (pattern, pattern, pattern)

    rows = await db.execute_fetchall(sql, params)
    return [dict(r) for r in rows]


def _search_team(
    query_lower: str,
    query_clean: str,
    members: list[dict],
    *,
    exact: bool,
) -> list[dict]:
    results: list[dict] = []
    for m in members:
        if exact:
            hit = (
                m["email"].lower() == query_lower
                or m["name"].lower() == query_lower
                or any(a.lower() == query_lower for a in m.get("aliases", []))
                or any(a.lower() == query_clean for a in m.get("accounts", []))
            )
        else:
            searchable = [
                m["name"].lower(),
                m["email"].lower(),
                *(a.lower() for a in m.get("aliases", [])),
                *(a.lower() for a in m.get("accounts", [])),
            ]
            hit = any(query_clean in s for s in searchable)

        if hit:
            results.append({
                "email": m["email"],
                "name": m["name"],
                "role": m.get("role", "external"),
                "aliases": m.get("aliases", []),
                "accounts": m.get("accounts", []),
            })
    return results


def _merge_and_dedup(
    event_rows: list[dict],
    team_hits: list[dict],
    all_members: list[dict],
    alias_map: dict[str, str],
    match_type: str,
) -> list[dict]:
    # Quick team lookup by any known email (primary or alias)
    team_by_email: dict[str, dict] = {}
    for m in all_members:
        team_by_email[m["email"].lower()] = m
        for a in m.get("aliases", []):
            team_by_email[a.lower()] = m

    contacts: dict[str, dict] = {}

    def _bucket(email: str) -> dict:
        canonical = alias_map.get(email.lower(), email.lower())
        if canonical not in contacts:
            contacts[canonical] = {
                "email": canonical,
                "names": [],
                "handles": [],
                "relationship": "",
                "platforms": [],
            }
        return contacts[canonical]

    # Fold in event rows
    for row in event_rows:
        email = row["actor_email"]
        if not email:
            continue
        c = _bucket(email)

        name = row.get("actor_name")
        if name and name not in c["names"]:
            c["names"].append(name)

        handle = row.get("actor_handle")
        if handle and handle not in c["handles"]:
            c["handles"].append(handle)

        platform = row.get("source_platform")
        if platform and platform not in c["platforms"]:
            c["platforms"].append(platform)

        rel = row.get("actor_relationship")
        if rel and not c["relationship"]:
            c["relationship"] = rel

    # Fold in team hits
    for tm in team_hits:
        c = _bucket(tm["email"])
        if tm["name"] and tm["name"] not in c["names"]:
            c["names"].append(tm["name"])
        for acct in tm.get("accounts", []):
            if acct and acct not in c["handles"]:
                c["handles"].append(acct)
        # Team role always wins
        c["relationship"] = tm.get("role", c["relationship"])

    # Enrich any event-sourced contacts that happen to match a team member
    for canonical, c in contacts.items():
        member = team_by_email.get(canonical)
        if not member:
            continue
        c["relationship"] = member.get("role", c["relationship"])
        if member["name"] not in c["names"]:
            c["names"].append(member["name"])
        for acct in member.get("accounts", []):
            if acct and acct not in c["handles"]:
                c["handles"].append(acct)

    result = [
        {**c, "match_type": match_type}
        for c in contacts.values()
    ]
    # More data points → higher relevance
    result.sort(key=lambda c: len(c["names"]) + len(c["platforms"]), reverse=True)
    return result[:CONTACT_SEARCH_MAX]
