# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for provider-refusal tracking (#25)."""

import httpx
import litellm
import pytest

from laya.llm import model_availability
from laya.llm.model_availability import (
    ModelUnavailableError,
    config_error_kind,
    known_unavailable,
    record_unavailable,
)
from tests.conftest import insert_test_event

_REQ = httpx.Request("POST", "https://provider.test")


def _not_found(msg="This model models/gemini-2.0-flash is no longer available."):
    return litellm.NotFoundError(message=msg, model="gemini-2.0-flash", llm_provider="gemini")


class TestConfigErrorKind:
    def test_404_401_403_map_to_settings_problems(self):
        """NotFound, Authentication and PermissionDenied map to not_found, auth, permission."""
        assert config_error_kind(_not_found()) == "not_found"
        assert config_error_kind(
            litellm.AuthenticationError(message="bad key", llm_provider="gemini", model="m")
        ) == "auth"
        assert config_error_kind(
            litellm.PermissionDeniedError(
                message="denied", llm_provider="openai", model="m",
                response=httpx.Response(403, request=_REQ),
            )
        ) == "permission"

    def test_gemini_400_mentioning_403_is_per_request(self):
        """A Gemini 400 that only mentions "403" is not a refusal."""
        quirk = litellm.BadRequestError(
            message="contents[403] is invalid", model="m", llm_provider="gemini",
            response=httpx.Response(403, request=_REQ),
        )
        assert config_error_kind(quirk) is None
        real = litellm.BadRequestError(
            message='{"code": 403, "status": "PERMISSION_DENIED"}', model="m",
            llm_provider="gemini", response=httpx.Response(403, request=_REQ),
        )
        assert config_error_kind(real) == "permission"

    def test_openrouter_moderation_403_is_per_request(self):
        """OpenRouter's moderation 403 is not a refusal."""
        exc = litellm.PermissionDeniedError(
            message="Your input was flagged by moderation", llm_provider="openrouter",
            model="m", response=httpx.Response(403, request=_REQ),
        )
        assert config_error_kind(exc) is None

    def test_per_request_errors_are_not_settings_problems(self):
        """400, 422, content policy, 429 and timeouts return None."""
        errors = [
            litellm.BadRequestError(message="bad", model="m", llm_provider="openai"),
            litellm.UnprocessableEntityError(
                message="bad", model="m", llm_provider="openai",
                response=httpx.Response(422, request=_REQ),
            ),
            litellm.ContentPolicyViolationError(message="no", model="m", llm_provider="openai"),
            litellm.RateLimitError(message="slow down", llm_provider="openai", model="m"),
            litellm.Timeout(message="timeout", model="m", llm_provider="openai"),
            RuntimeError("boom"),
        ]
        assert [config_error_kind(e) for e in errors] == [None] * len(errors)


@pytest.mark.asyncio
class TestRecordedRefusals:
    async def test_record_upserts_keeps_first_detected_at_and_broadcasts(self, db, monkeypatch):
        """A repeat refusal keeps detected_at and pushes the status."""
        sent: list[dict] = []

        async def fake_broadcast(message):
            sent.append(message)

        monkeypatch.setattr("laya.api.websocket.manager.broadcast", fake_broadcast)

        err = await record_unavailable(_not_found(), "gemini/gemini-2.0-flash", "router", "default")
        assert isinstance(err, ModelUnavailableError)
        assert err.kind == "not_found"
        await db.execute("UPDATE model_availability SET detected_at = '2000-01-01 00:00:00'")
        await db.commit()

        await record_unavailable(_not_found("still gone"), "gemini/gemini-2.0-flash", "router", "default")

        rows = await db.execute_fetchall("SELECT reason, detected_at FROM model_availability")
        assert len(rows) == 1
        assert "still gone" in rows[0]["reason"]
        assert rows[0]["detected_at"] == "2000-01-01 00:00:00"
        assert [m["type"] for m in sent] == ["model_availability", "model_availability"]
        assert sent[0]["payload"]["unavailable"][0]["model"] == "gemini/gemini-2.0-flash"

    async def test_record_never_raises_when_db_write_fails(self, db, monkeypatch):
        """A failed write still returns the typed error."""
        async def broken_db():
            raise RuntimeError("db gone")

        monkeypatch.setattr(model_availability, "get_db", broken_db)
        err = await record_unavailable(_not_found(), "gemini/gemini-2.0-flash", "router", "")
        assert isinstance(err, ModelUnavailableError)

    async def test_record_ignores_per_request_errors(self, db):
        """A per-request error is not recorded."""
        err = await record_unavailable(RuntimeError("boom"), "gemini/x", "router", "")
        assert err is None
        assert await db.execute_fetchall("SELECT * FROM model_availability") == []

    async def test_known_unavailable_is_scoped_by_model_role_and_space(self, db):
        """A refusal in one space doesn't block another."""
        await record_unavailable(
            litellm.AuthenticationError(message="bad key", llm_provider="gemini", model="m"),
            "gemini/gemini-3.8-flash", "stager", "work",
        )
        assert await known_unavailable("gemini/gemini-3.8-flash", "stager", "work") is not None
        assert await known_unavailable("gemini/gemini-3.8-flash", "stager", "default") is None
        assert await known_unavailable("gemini/gemini-3.8-flash", "chat", "work") is None
        assert await known_unavailable("gemini/other", "stager", "work") is None

    async def test_status_lists_refusals_and_counts_held_events(self, db):
        """status() returns the refusals and the held-event count."""
        await record_unavailable(_not_found(), "gemini/gemini-2.0-flash", "router", "default")
        await insert_test_event(db, "evt_held")
        await db.execute("UPDATE events SET processing_status = 'held' WHERE event_id = 'evt_held'")
        await db.commit()

        result = await model_availability.status()
        assert result["held_events"] == 1
        assert result["unavailable"][0]["kind"] == "not_found"
        assert result["unavailable"][0]["role"] == "router"

    async def test_clear_forgets_all_refusals(self, db):
        """clear() empties the table."""
        await record_unavailable(_not_found(), "gemini/gemini-2.0-flash", "router", "")
        await model_availability.clear()
        assert await known_unavailable("gemini/gemini-2.0-flash", "router", "") is None
