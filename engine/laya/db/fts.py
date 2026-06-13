# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""SQLite FTS5 full-text search infrastructure for BM25-ranked keyword retrieval.

The lexical half of Laya's hybrid retrieval (chat, the card-search tool, trace)
used SQL ``LIKE`` with recency ordering — no term-frequency ranking at all. This
module adds FTS5 virtual tables (``cards_fts``, ``events_fts``) kept in sync with
their base tables via triggers, queried with the ``bm25()`` ranker. Callers query
FTS when it is available and fall back to ``LIKE`` otherwise (e.g. an exotic SQLite
build compiled without the fts5 module), so retrieval never hard-fails.

The card index includes ``thread_context`` (persisted by emit.py) so the lexical
side gets the same "Contextual BM25" referent restoration as the vector side — a
terse follow-up card becomes findable by terms from its thread, not just its own
sparse text. See ``project_rag_contextual_retrieval`` and the RAG Phase 2 plan.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger()

# Whether the FTS5 tables were created successfully this process. Callers gate on
# this for a fast path; they also wrap the FTS query itself in try/except as a
# safety net, so a runtime FTS error still degrades to LIKE rather than 500ing.
_FTS_READY = False


def fts_ready() -> bool:
    """True if the FTS5 tables are available for querying this process."""
    return _FTS_READY


# Lightweight English stopwords — kept local so the FTS layer has no import
# dependency on the chat/trace pipelines (it mirrors the sets defined there).
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "am", "i", "me",
    "my", "we", "our", "you", "your", "he", "she", "it", "they", "them",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "how", "when", "where", "why", "and", "or", "but", "not", "no",
    "if", "then", "so", "to", "of", "in", "on", "at", "by", "for",
    "with", "about", "from", "up", "out", "into", "over", "after",
    "all", "any", "some", "just", "also", "than", "very", "too",
})


def build_fts_match(query: str, *, min_len: int = 3, max_terms: int = 8) -> str | None:
    """Build a safe FTS5 MATCH expression (OR of quoted phrases) from free text.

    Each surviving token is wrapped in double quotes (internal quotes doubled) so
    FTS5 treats it as a literal phrase. This neutralises FTS5 query operators that
    may appear in user input (``*``, ``:``, ``^``, ``AND``/``OR``/``NOT``, parens),
    which would otherwise raise a syntax error inside MATCH. Unqualified phrases
    match across all indexed columns of the target table.

    Returns None when no usable terms remain (stopwords-only / too short), so the
    caller can skip FTS entirely rather than issue an empty match.
    """
    if not query:
        return None
    terms: list[str] = []
    for w in query.split():
        if len(w) < min_len:
            continue
        if w.lower() in _STOPWORDS:
            continue
        if not any(ch.isalnum() for ch in w):
            continue
        terms.append(w)
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(quoted)


# Standalone FTS5 tables (own-content) + triggers that mirror every base-table
# mutation. UNINDEXED id columns are stored for the join-back but not tokenised.
# 'porter unicode61' adds light stemming (crash/crashes/crashing collapse) on top
# of unicode-aware tokenisation, improving keyword recall.
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
    card_id UNINDEXED,
    header,
    summary,
    intelligence,
    thread_context,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS cards_fts_ai AFTER INSERT ON action_cards BEGIN
    INSERT INTO cards_fts(card_id, header, summary, intelligence, thread_context)
    VALUES (new.card_id, COALESCE(new.header, ''), COALESCE(new.summary, ''),
            COALESCE(new.intelligence, ''), COALESCE(new.thread_context, ''));
END;

CREATE TRIGGER IF NOT EXISTS cards_fts_ad AFTER DELETE ON action_cards BEGIN
    DELETE FROM cards_fts WHERE card_id = old.card_id;
END;

CREATE TRIGGER IF NOT EXISTS cards_fts_au AFTER UPDATE ON action_cards BEGIN
    DELETE FROM cards_fts WHERE card_id = old.card_id;
    INSERT INTO cards_fts(card_id, header, summary, intelligence, thread_context)
    VALUES (new.card_id, COALESCE(new.header, ''), COALESCE(new.summary, ''),
            COALESCE(new.intelligence, ''), COALESCE(new.thread_context, ''));
END;

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    event_id UNINDEXED,
    subject_title,
    content_body,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS events_fts_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(event_id, subject_title, content_body)
    VALUES (new.event_id, COALESCE(new.subject_title, ''), COALESCE(new.content_body, ''));
END;

CREATE TRIGGER IF NOT EXISTS events_fts_ad AFTER DELETE ON events BEGIN
    DELETE FROM events_fts WHERE event_id = old.event_id;
END;

CREATE TRIGGER IF NOT EXISTS events_fts_au AFTER UPDATE ON events BEGIN
    DELETE FROM events_fts WHERE event_id = old.event_id;
    INSERT INTO events_fts(event_id, subject_title, content_body)
    VALUES (new.event_id, COALESCE(new.subject_title, ''), COALESCE(new.content_body, ''));
END;
"""


async def ensure_fts_tables(db) -> None:
    """Create the FTS5 tables + sync triggers and backfill existing rows.

    Idempotent — runs at startup after migrations (and in tests after the migration
    runner). On a SQLite build without the fts5 module the CREATE raises; we log and
    leave ``_FTS_READY`` False so every caller transparently uses LIKE instead.
    """
    global _FTS_READY
    try:
        await db.executescript(_FTS_DDL)

        # Backfill pre-existing rows the first time each index is created. Triggers
        # cover every subsequent mutation, so this only runs while the index is
        # empty (guarding against re-inserting on later startups).
        card_rows = await db.execute_fetchall("SELECT COUNT(*) AS c FROM cards_fts")
        if card_rows and card_rows[0]["c"] == 0:
            await db.execute(
                """INSERT INTO cards_fts(card_id, header, summary, intelligence, thread_context)
                   SELECT card_id, COALESCE(header, ''), COALESCE(summary, ''),
                          COALESCE(intelligence, ''), COALESCE(thread_context, '')
                   FROM action_cards"""
            )
        event_rows = await db.execute_fetchall("SELECT COUNT(*) AS c FROM events_fts")
        if event_rows and event_rows[0]["c"] == 0:
            await db.execute(
                """INSERT INTO events_fts(event_id, subject_title, content_body)
                   SELECT event_id, COALESCE(subject_title, ''), COALESCE(content_body, '')
                   FROM events"""
            )
        await db.commit()
        _FTS_READY = True
        log.info("fts_tables_ready")
    except Exception as e:
        # fts5 module missing or DDL failed — keep serving via the LIKE fallback.
        _FTS_READY = False
        log.warning("fts_unavailable_fallback_like", error=str(e))
