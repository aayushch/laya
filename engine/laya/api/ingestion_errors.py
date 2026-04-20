"""Ingestion errors API — receives failure reports from n8n's shared Error Trigger workflow.

n8n's "Laya - Error Handler" workflow is wired as `settings.errorWorkflow` on every
ingestion workflow clone. When any node in an ingestion workflow fails (bad creds,
API rate limit, code exception, engine POST failing), n8n invokes the error handler,
which POSTs a structured report to POST /ingestion-errors.

Coalescing: identical failures (same workflow_id + node_name + error fingerprint)
seen within the dedup window update an existing row's occurrence_count instead of
inserting a new one. This caps row volume when a broken credential polls every
60 s (1440 potential rows/day → 1 row with occurrence_count climbing).

Scope: capture only. Surfacing (UI banner, per-source indicator) is a follow-up
milestone — the data model has `acknowledged_at` / `resolved_at` columns ready
for that work.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from laya.db.sqlite import get_db

log = structlog.get_logger()
router = APIRouter()


# Dedup window: failures with the same fingerprint on the same (workflow, node)
# within this window update an existing row instead of inserting a new one.
COALESCE_WINDOW_MINUTES = 30

# Strip long numeric sequences (trace IDs, timestamps embedded in messages) so
# messages that differ only in those substrings share a fingerprint.
_LONG_DIGITS_RE = re.compile(r"\d{4,}")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_message(message: str) -> str:
    if not message:
        return ""
    normalized = _LONG_DIGITS_RE.sub("N", message)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def _fingerprint(error_name: str | None, error_message: str | None) -> str:
    payload = f"{error_name or ''}|{_normalize_message(error_message or '')}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ── request / response models ────────────────────────────────────────────────


class IngestionErrorReport(BaseModel):
    """Payload POSTed by the n8n error handler workflow."""

    workflow_id: str = Field(..., description="n8n $workflow.id of the failing workflow")
    error_message: str
    occurred_at: datetime

    # Optional — populated by the n8n error handler on a best-effort basis.
    error_id: Optional[str] = Field(
        default=None,
        description="Client-supplied idempotency key; server generates if absent",
    )
    workflow_name: Optional[str] = None
    platform: Optional[str] = None
    node_name: Optional[str] = None
    node_type: Optional[str] = None
    error_name: Optional[str] = None
    error_http_code: Optional[int] = None
    error_details: Optional[Any] = None  # arbitrary JSON — stored as TEXT
    error_stack: Optional[str] = None
    execution_id: Optional[str] = None
    execution_url: Optional[str] = None
    execution_mode: Optional[str] = None


class IngestionErrorResponse(BaseModel):
    error_id: str
    coalesced: bool


class IngestionErrorRow(BaseModel):
    error_id: str
    workflow_id: str
    source_id: Optional[str]
    space_id: Optional[str]
    platform: Optional[str]
    workflow_name: Optional[str]
    node_name: Optional[str]
    node_type: Optional[str]
    error_name: Optional[str]
    error_message: Optional[str]
    error_http_code: Optional[int]
    error_details: Optional[Any]
    execution_id: Optional[str]
    execution_url: Optional[str]
    execution_mode: Optional[str]
    occurrence_count: int
    first_occurred_at: str
    last_occurred_at: str
    occurred_at: str
    acknowledged_at: Optional[str]
    resolved_at: Optional[str]


# ── endpoints ────────────────────────────────────────────────────────────────


@router.post("/ingestion-errors", response_model=IngestionErrorResponse, status_code=202)
async def report_ingestion_error(report: IngestionErrorReport) -> IngestionErrorResponse:
    """Receive an ingestion failure report from n8n's error handler workflow.

    Flow:
    1. Compute a fingerprint from error_name + normalized message.
    2. Resolve source_id + space_id read-only from the sources table (no auto-create).
    3. If a matching row exists within the coalesce window, bump occurrence_count.
    4. Otherwise insert a new row.
    """
    db = await get_db()
    fingerprint = _fingerprint(report.error_name, report.error_message)
    occurred_at_iso = report.occurred_at.astimezone(timezone.utc).isoformat()

    # 1. Resolve source + space by workflow_id. Deliberately NOT calling
    # space_resolution.resolve_space() — that has side effects (auto-inserting
    # rows into `sources`), which would be surprising from an error-report path.
    # If the source doesn't exist, the error stays orphaned (both fields NULL)
    # and we log it for debuggability.
    source_id: Optional[str] = None
    space_id: Optional[str] = None
    source_rows = await db.execute_fetchall(
        "SELECT source_id, space_id FROM sources WHERE workflow_id = ?",
        (report.workflow_id,),
    )
    if source_rows:
        source_id = source_rows[0]["source_id"]
        space_id = source_rows[0]["space_id"]
    else:
        log.warning(
            "ingestion_error_unresolved_source",
            workflow_id=report.workflow_id,
            platform=report.platform,
        )

    # 2. Coalesce: look for an existing row matching (workflow, node, fingerprint)
    # within the dedup window. If the most-recent match is acknowledged AND the
    # ack predates the window, treat this as a fresh row (user has moved on; a
    # new occurrence should appear as a new row).
    existing = await db.execute_fetchall(
        f"""
        SELECT error_id, occurrence_count
        FROM ingestion_errors
        WHERE workflow_id = ? AND node_name IS ? AND fingerprint = ?
          AND last_occurred_at > datetime('now', '-{COALESCE_WINDOW_MINUTES} minutes')
          AND (acknowledged_at IS NULL
               OR last_occurred_at > datetime('now', '-{COALESCE_WINDOW_MINUTES} minutes'))
        ORDER BY last_occurred_at DESC
        LIMIT 1
        """,
        (report.workflow_id, report.node_name, fingerprint),
    )

    if existing:
        error_id = existing[0]["error_id"]
        await db.execute(
            """
            UPDATE ingestion_errors
               SET occurrence_count = occurrence_count + 1,
                   last_occurred_at = ?,
                   error_message    = ?,
                   error_http_code  = COALESCE(?, error_http_code),
                   error_details    = COALESCE(?, error_details),
                   execution_id     = COALESCE(?, execution_id),
                   execution_url    = COALESCE(?, execution_url)
             WHERE error_id = ?
            """,
            (
                occurred_at_iso,
                report.error_message,
                report.error_http_code,
                _json_or_none(report.error_details),
                report.execution_id,
                report.execution_url,
                error_id,
            ),
        )
        await db.commit()
        log.warning(
            "ingestion_error_captured",
            status="coalesced",
            error_id=error_id,
            workflow_id=report.workflow_id,
            platform=report.platform,
            node=report.node_name,
            http=report.error_http_code,
            msg=report.error_message[:200],
        )
        return IngestionErrorResponse(error_id=error_id, coalesced=True)

    # 3. Insert new row. Use the client-supplied idempotency key if present, else
    # mint one. We don't rely on the client key for correctness (the fingerprint
    # + window is the dedup mechanism); it's just a stable PK when the n8n handler
    # retries the POST after a transient engine error.
    error_id = report.error_id or f"ierr_{uuid.uuid4().hex[:16]}"
    try:
        await db.execute(
            """
            INSERT INTO ingestion_errors (
                error_id, workflow_id, source_id, space_id, platform,
                workflow_name, node_name, node_type, error_name, error_message,
                error_http_code, error_details, execution_id, execution_url,
                execution_mode, fingerprint, occurrence_count,
                first_occurred_at, last_occurred_at, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                error_id,
                report.workflow_id,
                source_id,
                space_id,
                report.platform,
                report.workflow_name,
                report.node_name,
                report.node_type,
                report.error_name,
                report.error_message,
                report.error_http_code,
                _json_or_none(report.error_details),
                report.execution_id,
                report.execution_url,
                report.execution_mode,
                fingerprint,
                occurred_at_iso,
                occurred_at_iso,
                occurred_at_iso,
            ),
        )
        await db.commit()
    except Exception as e:
        # UNIQUE-violation on client-supplied error_id → treat as a duplicate
        # retry. Fetch the existing row and return it as coalesced.
        if "UNIQUE" in str(e):
            log.info("ingestion_error_duplicate_id", error_id=error_id)
            return IngestionErrorResponse(error_id=error_id, coalesced=True)
        raise

    log.warning(
        "ingestion_error_captured",
        status="new",
        error_id=error_id,
        workflow_id=report.workflow_id,
        platform=report.platform,
        node=report.node_name,
        http=report.error_http_code,
        msg=report.error_message[:200],
    )
    return IngestionErrorResponse(error_id=error_id, coalesced=False)


@router.get("/ingestion-errors")
async def list_ingestion_errors(
    space_id: Optional[str] = Query(default=None),
    source_id: Optional[str] = Query(default=None),
    unacknowledged_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List captured ingestion errors. Used by the surfacing layer."""
    db = await get_db()

    where: list[str] = []
    params: list[Any] = []
    if space_id is not None:
        where.append("space_id = ?")
        params.append(space_id)
    if source_id is not None:
        where.append("source_id = ?")
        params.append(source_id)
    if unacknowledged_only:
        where.append("acknowledged_at IS NULL")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.extend([limit, offset])

    rows = await db.execute_fetchall(
        f"""
        SELECT * FROM ingestion_errors
        {where_sql}
        ORDER BY last_occurred_at DESC
        LIMIT ? OFFSET ?
        """,
        params,
    )
    return {"errors": [_row_to_model(r).model_dump() for r in rows]}


# ── helpers ──────────────────────────────────────────────────────────────────


def _json_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _row_to_model(row: Any) -> IngestionErrorRow:
    details = row["error_details"]
    if details:
        try:
            details = json.loads(details)
        except (TypeError, ValueError):
            pass  # keep as raw string

    return IngestionErrorRow(
        error_id=row["error_id"],
        workflow_id=row["workflow_id"],
        source_id=row["source_id"],
        space_id=row["space_id"],
        platform=row["platform"],
        workflow_name=row["workflow_name"],
        node_name=row["node_name"],
        node_type=row["node_type"],
        error_name=row["error_name"],
        error_message=row["error_message"],
        error_http_code=row["error_http_code"],
        error_details=details,
        execution_id=row["execution_id"],
        execution_url=row["execution_url"],
        execution_mode=row["execution_mode"],
        occurrence_count=row["occurrence_count"],
        first_occurred_at=row["first_occurred_at"],
        last_occurred_at=row["last_occurred_at"],
        occurred_at=row["occurred_at"],
        acknowledged_at=row["acknowledged_at"],
        resolved_at=row["resolved_at"],
    )
