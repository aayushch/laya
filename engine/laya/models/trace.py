"""Pydantic models for the Trace feature (semantic cross-platform entity search)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from laya.models.card import CardResponse


class TraceRequest(BaseModel):
    """Input for running a new trace."""

    query: str = Field(min_length=1, max_length=500)
    space_id: str | None = None  # None = cross-space search
    include_archived: bool = True
    max_results: int = Field(default=50, ge=1, le=200)
    fuzzy_search: bool = False  # Broad keyword-split LIKE search (noisy, off by default)

    # Advanced search toggles — all default True for backward compatibility.
    # When disabled, the corresponding pipeline stage is skipped entirely.
    enable_semantic: bool = True     # ChromaDB vector similarity search
    enable_text: bool = True         # SQLite phrase-match on card content (header, summary, etc.)
    enable_identifier: bool = True   # Regex pattern matching (PR-540, LAYA-986, etc.) — always cheap
    enable_entity: bool = True       # Entity table lookup by canonical_name
    enable_llm_filter: bool = True   # LLM-based relevance filtering after expansion


class TraceEntity(BaseModel):
    """An entity discovered in the trace."""

    entity_id: str
    title: str
    url: str | None = None
    platform: str


class TraceChapter(BaseModel):
    """A logical phase/chapter in the entity lifecycle."""

    label: str  # "Created", "Discussion", "Code Review", "Resolved", etc.
    timestamp: str  # ISO datetime of the first card in the chapter
    card_ids: list[str]


class TraceStatusSummary(BaseModel):
    """Aggregated status across all cards in a trace cluster."""

    current_state: str  # derived from the latest card's status/event type
    platforms_involved: list[str]
    total_cards: int
    date_range: dict[str, str]  # {"from": "2026-03-15", "to": "2026-03-28"}
    pending_actions: int


class TraceCluster(BaseModel):
    """A group of related entities and their cards forming one trace result."""

    cluster_id: str
    primary_entity: TraceEntity
    linked_entities: list[TraceEntity] = Field(default_factory=list)
    narrative: str | None = None
    chapters: list[TraceChapter] = Field(default_factory=list)
    timeline: list[CardResponse] = Field(default_factory=list)
    status_summary: TraceStatusSummary


class SearchMetadata(BaseModel):
    """Diagnostic info about the search execution."""

    semantic_hits: int = 0
    fuzzy_hits: int = 0
    entity_hits: int = 0
    expansion_cards: int = 0
    elapsed_ms: int = 0
    fuzzy_search: bool = False
    enable_semantic: bool = True
    enable_text: bool = True
    enable_llm_filter: bool = True
    avg_semantic_distance: float | None = None
    seeds_filtered: int = 0
    feedback_excluded: int = 0
    feedback_demoted: int = 0


class TraceResponse(BaseModel):
    """Full trace result returned to the client."""

    trace_id: str
    query: str
    clusters: list[TraceCluster]
    search_metadata: SearchMetadata
    created_at: str
    summary: str | None = None
    space_id: str | None = None


class TraceListItem(BaseModel):
    """Summary item for trace history listing."""

    trace_id: str
    query: str
    created_at: str
    total_cards: int
    platforms: list[str]
    fuzzy_search: bool = False
    enable_semantic: bool = True
    enable_text: bool = True
    enable_llm_filter: bool = True
