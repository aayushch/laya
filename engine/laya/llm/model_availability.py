# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Models and API keys a provider refused outright, stored in the DB (#25)."""

from __future__ import annotations

from typing import Any

import structlog

from laya.db.sqlite import get_db

log = structlog.get_logger()

_KIND_BY_STATUS: dict[int, str] = {401: "auth", 403: "permission", 404: "not_found"}
_REASON_MAX_CHARS = 300


class ModelUnavailableError(Exception):
    """The provider refused this model or key; retrying can't succeed."""

    def __init__(self, model: str, role: str, kind: str, reason: str) -> None:
        super().__init__(f"{model} ({role}): {reason}")
        self.model = model
        self.role = role
        self.kind = kind
        self.reason = reason


def _http_status(exc: BaseException) -> int | None:
    """Return a provider error's HTTP status, preferring ``exc.response`` (litellm wraps some 403s in BadRequestError)."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def config_error_kind(exc: BaseException) -> str | None:
    """Return "not_found", "auth" or "permission" for a settings problem, else None."""
    import litellm

    kind = _KIND_BY_STATUS.get(_http_status(exc))
    if kind == "permission":
        text = str(exc)
        # litellm's Gemini mapper raises BadRequestError for any error text containing
        # "403", so only a real PERMISSION_DENIED counts.
        if isinstance(exc, litellm.BadRequestError) and "PERMISSION_DENIED" not in text:
            return None
        # OpenRouter's moderation 403 is about one input, not the key.
        if "flagged" in text.lower():
            return None
    return kind


async def known_unavailable(model: str, role: str, space_id: str) -> ModelUnavailableError | None:
    """Return the recorded refusal for this model, role and space, if any."""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT kind, reason FROM model_availability WHERE model = ? AND role = ? AND space_id = ?",
        (model, role, space_id),
    )
    if not rows:
        return None
    return ModelUnavailableError(model, role, rows[0]["kind"], rows[0]["reason"])


async def record_unavailable(
    exc: BaseException, model: str, role: str, space_id: str
) -> ModelUnavailableError | None:
    """Record ``exc`` if it is a settings problem and return the error to raise.

    Never raises, so a DB error can't mask the provider's error.
    """
    kind = config_error_kind(exc)
    if kind is None:
        return None
    reason = str(exc)[:_REASON_MAX_CHARS]
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO model_availability (model, role, space_id, kind, reason)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(model, role, space_id) DO UPDATE SET
                   kind = excluded.kind, reason = excluded.reason""",
            (model, role, space_id, kind, reason),
        )
        await db.commit()
        log.warning("model_marked_unavailable", model=model, role=role, space_id=space_id, kind=kind)
        await broadcast_status()
    except Exception as e:
        log.warning("model_unavailable_record_failed", model=model, role=role, error=str(e))
    return ModelUnavailableError(model, role, kind, reason)


async def clear() -> None:
    """Forget all recorded refusals."""
    db = await get_db()
    await db.execute("DELETE FROM model_availability")
    await db.commit()


async def status() -> dict[str, Any]:
    """Return the refusals and the held-event count (the /health and WS payload)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT model, role, space_id, kind, reason, detected_at
           FROM model_availability ORDER BY detected_at, model, role"""
    )
    held = await db.execute_fetchall(
        "SELECT COUNT(*) AS n FROM events WHERE processing_status = 'held'"
    )
    return {
        "unavailable": [dict(r) for r in rows],
        "held_events": held[0]["n"] if held else 0,
    }


async def broadcast_status() -> None:
    """Push ``status()`` over the WebSocket; best-effort."""
    try:
        from laya.api.websocket import manager

        await manager.broadcast({"type": "model_availability", "payload": await status()})
    except Exception as e:
        log.warning("model_availability_broadcast_failed", error=str(e))
