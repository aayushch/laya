# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Canonical timestamp format: helper, date filters, and the normalization migration.

Locks in the fix for the "chat can't find today's cards" bug: date bounds and
stored timestamps must both be space-separated UTC (`%Y-%m-%d %H:%M:%S`), so a
same-day range matches instead of silently returning zero rows (a `.isoformat()`
`T`-bound sorts after every space-format value because ' ' (0x20) < 'T' (0x54)).
"""

from datetime import datetime, timedelta, timezone

import pytest

from laya.config import MIGRATIONS_DIR
from laya.db.timeutil import DB_TS_FMT, db_now, db_ts, db_ts_from_epoch
from laya.llm.tools import card_tools
from laya.llm.tools.card_tools import search_cards, _filter_conditions
from laya.llm.tools.constants import parse_iso_to_timestamp
from tests.conftest import insert_test_card


# --- helper format ---------------------------------------------------------

def test_db_now_is_space_format_utc():
    s = db_now()
    assert "T" not in s and "+" not in s and "Z" not in s
    # parses back with the canonical format
    datetime.strptime(s, DB_TS_FMT)


def test_db_ts_naive_treated_as_utc():
    assert db_ts(datetime(2026, 7, 1, 8, 30, 0)) == "2026-07-01 08:30:00"


def test_db_ts_aware_converted_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert db_ts(datetime(2026, 7, 1, 14, 0, 0, tzinfo=ist)) == "2026-07-01 08:30:00"


def test_db_ts_from_epoch():
    epoch = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert db_ts_from_epoch(epoch) == "2026-07-01 00:00:00"


# --- date-bound parsing ----------------------------------------------------

def test_bare_date_lower_bound_is_start_of_day():
    ts = parse_iso_to_timestamp("2026-07-01")
    assert db_ts_from_epoch(ts) == "2026-07-01 00:00:00"


def test_bare_date_upper_bound_extends_to_end_of_day():
    ts = parse_iso_to_timestamp("2026-07-01", end_of_day=True)
    assert db_ts_from_epoch(ts) == "2026-07-01 23:59:59"


def test_end_of_day_ignored_for_full_datetime():
    ts = parse_iso_to_timestamp("2026-07-01T08:00:00", end_of_day=True)
    assert db_ts_from_epoch(ts) == "2026-07-01 08:00:00"


def test_filter_conditions_bounds_are_space_format():
    dfrom = parse_iso_to_timestamp("2026-07-01")
    dto = parse_iso_to_timestamp("2026-07-01", end_of_day=True)
    conds, params = _filter_conditions(None, None, None, dfrom, dto)
    assert conds == ["created_at >= ?", "created_at <= ?"]
    assert params == ["2026-07-01 00:00:00", "2026-07-01 23:59:59"]
    assert all("T" not in p and "+" not in p for p in params)


# --- regression: search_cards "today" range --------------------------------

@pytest.mark.asyncio
async def test_search_cards_today_range_returns_todays_card(db):
    """A same-day date range must return that day's cards (was 0 before the fix)."""
    await insert_test_card(db, card_id="card_today", event_id="evt_today",
                           entity_id="jira:ticket:T-1")
    await insert_test_card(db, card_id="card_old", event_id="evt_old",
                           entity_id="jira:ticket:T-2")
    # late-in-the-day card verifies the end-of-day upper bound
    await insert_test_card(db, card_id="card_late", event_id="evt_late",
                           entity_id="jira:ticket:T-3")
    await db.execute("UPDATE action_cards SET created_at=? WHERE card_id=?",
                     ("2026-07-01 10:00:00", "card_today"))
    await db.execute("UPDATE action_cards SET created_at=? WHERE card_id=?",
                     ("2026-07-01 23:30:00", "card_late"))
    await db.execute("UPDATE action_cards SET created_at=? WHERE card_id=?",
                     ("2026-06-15 10:00:00", "card_old"))
    await db.commit()

    res = await search_cards(date_from="2026-07-01", date_to="2026-07-01", semantic=False)
    ids = {c["card_id"] for g in res["groups"] for c in g["cards"]}
    assert "card_today" in ids
    assert "card_late" in ids          # end-of-day bound includes 23:30
    assert "card_old" not in ids       # a different day is excluded


# --- the normalization migration -------------------------------------------

@pytest.mark.asyncio
async def test_migration_071_normalizes_iso_timestamps(db):
    """Seed legacy `T`-format values, re-run the migration, assert they become
    canonical space-format (idempotent thanks to the LIKE '%T%' guard)."""
    await insert_test_card(db, card_id="c1", event_id="e1", entity_id="jira:ticket:M-1")
    # mixed legacy formats across a few migrated columns
    await db.execute(
        "UPDATE action_cards SET resolved_at=?, read_at=?, updated_at=? WHERE card_id='c1'",
        ("2026-05-11T03:23:27.802202+00:00", "2026-05-06T08:55:22.949211+00:00",
         "2026-05-11T03:23:27+00:00"),
    )
    await db.execute("UPDATE events SET timestamp=? WHERE event_id='e1'",
                     ("2026-04-04T21:27:17+00:00",))
    await db.commit()

    sql = (MIGRATIONS_DIR / "071_normalize_timestamp_formats.sql").read_text(encoding="utf-8")
    await db.executescript(sql)
    await db.commit()

    row = (await db.execute_fetchall(
        "SELECT resolved_at, read_at, updated_at FROM action_cards WHERE card_id='c1'"))[0]
    assert row["resolved_at"] == "2026-05-11 03:23:27"
    assert row["read_at"] == "2026-05-06 08:55:22"
    assert row["updated_at"] == "2026-05-11 03:23:27"
    ev = (await db.execute_fetchall("SELECT timestamp FROM events WHERE event_id='e1'"))[0]
    assert ev["timestamp"] == "2026-04-04 21:27:17"

    # and nothing 'T'-shaped survives in the columns the migration targets
    for tbl, col in [("action_cards", "resolved_at"), ("action_cards", "read_at"),
                     ("action_cards", "updated_at"), ("events", "timestamp")]:
        left = (await db.execute_fetchall(
            f"SELECT COUNT(*) AS n FROM {tbl} WHERE {col} LIKE '%T%'"))[0]["n"]
        assert left == 0, f"{tbl}.{col} still has T-format rows"


@pytest.mark.asyncio
async def test_migration_071_never_nulls_unparseable(db):
    """COALESCE guard: a value strftime can't parse is left untouched, not nulled."""
    await insert_test_card(db, card_id="c2", event_id="e2", entity_id="jira:ticket:M-2")
    # contains a 'T' (so the WHERE matches) but is not a valid datetime
    await db.execute("UPDATE action_cards SET read_at=? WHERE card_id='c2'",
                     ("NOT-A-TIMESTAMP",))
    await db.commit()

    sql = (MIGRATIONS_DIR / "071_normalize_timestamp_formats.sql").read_text(encoding="utf-8")
    await db.executescript(sql)
    await db.commit()

    row = (await db.execute_fetchall(
        "SELECT read_at FROM action_cards WHERE card_id='c2'"))[0]
    assert row["read_at"] == "NOT-A-TIMESTAMP"  # preserved, not nulled
