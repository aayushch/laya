# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for holding events on refused models and keys (#25)."""

from unittest.mock import patch

import litellm
import pytest
from httpx import ASGITransport, AsyncClient

from laya.llm import model_availability
from laya.llm.model_availability import ModelUnavailableError, record_unavailable
from laya.pipeline import queue
from tests.conftest import insert_test_event


def _gemini_404():
    return litellm.NotFoundError(
        message="This model models/gemini-2.0-flash is no longer available.",
        model="gemini-2.0-flash",
        llm_provider="gemini",
    )


async def _queued_event(db, sample_event):
    """Insert sample_event as a queued event with a loadable raw_json."""
    await insert_test_event(db, sample_event.event_id)
    await db.execute(
        "UPDATE events SET raw_json = ?, processing_status = 'queued' WHERE event_id = ?",
        (sample_event.model_dump_json(), sample_event.event_id),
    )
    await db.commit()


async def _held_event(db, event_id="evt_held"):
    """Insert an event already on hold behind a recorded refusal."""
    await insert_test_event(db, event_id)
    await db.execute(
        "UPDATE events SET processing_status = 'held' WHERE event_id = ?", (event_id,)
    )
    await db.commit()
    await record_unavailable(_gemini_404(), "gemini/gemini-2.0-flash", "router", "default")


async def _status(db, event_id="evt_held"):
    rows = await db.execute_fetchall(
        "SELECT processing_status, processing_attempts FROM events WHERE event_id = ?",
        (event_id,),
    )
    return rows[0]["processing_status"], rows[0]["processing_attempts"]


def _client():
    from laya.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestHoldAndRelease:
    async def test_refused_model_holds_event_and_refunds_attempt(self, db, sample_event):
        """A refused model holds the event without using an attempt."""
        await _queued_event(db, sample_event)
        refusal = ModelUnavailableError("gemini/gemini-2.0-flash", "router", "not_found", "gone")

        with patch("laya.pipeline.ingest.run_ingest", side_effect=refusal):
            await queue.process_event(sample_event.event_id)

        assert await _status(db, sample_event.event_id) == ("held", 0)

    async def test_other_errors_keep_retry_then_dead_path(self, db, sample_event):
        """Other errors still retry, then go dead."""
        await _queued_event(db, sample_event)

        with patch("laya.pipeline.ingest.run_ingest", side_effect=RuntimeError("boom")):
            await queue.process_event(sample_event.event_id)

        assert await _status(db, sample_event.event_id) == ("retrying", 1)

    async def test_held_events_are_never_fetched(self, db, sample_event):
        """Held events are not picked up by the consumer."""
        await _held_event(db)
        assert "evt_held" not in await queue._fetch_ready_events(limit=10)

    async def test_release_requeues_clears_and_broadcasts(self, db, monkeypatch):
        """Release re-queues held events, clears refusals and pushes status."""
        sent: list[dict] = []

        async def fake_broadcast(message):
            sent.append(message)

        await _held_event(db)
        monkeypatch.setattr("laya.api.websocket.manager.broadcast", fake_broadcast)

        assert await queue.release_held_events() == 1

        assert (await _status(db))[0] == "queued"
        assert await db.execute_fetchall("SELECT * FROM model_availability") == []
        assert sent[-1] == {
            "type": "model_availability",
            "payload": {"unavailable": [], "held_events": 0},
        }


@pytest.mark.asyncio
class TestReleaseTriggers:
    async def test_retry_held_endpoint_releases(self, db):
        """POST /events/held/retry releases held events."""
        await _held_event(db)
        async with _client() as client:
            resp = await client.post("/events/held/retry")
        assert resp.status_code == 200
        assert resp.json() == {"released": 1}
        assert (await _status(db))[0] == "queued"

    async def test_put_settings_releases_only_when_models_change(self, db):
        """PUT /settings releases only when the models change."""
        await _held_event(db)
        current = {"models": {"router": "gemini/gemini-2.0-flash"}, "theme": "dark"}
        with patch("laya.api.settings_api.load_settings", side_effect=lambda: {
            k: dict(v) if isinstance(v, dict) else v for k, v in current.items()
        }), patch("laya.api.settings_api.save_settings"):
            async with _client() as client:
                await client.put("/settings", json={"theme": "light"})
                assert (await _status(db))[0] == "held"
                await client.put("/settings", json={"models": {"router": "gemini/gemini-2.0-flash"}})
                assert (await _status(db))[0] == "held"
                await client.put("/settings", json={"models": {"router": "gemini/gemini-3.6-flash"}})
        assert (await _status(db))[0] == "queued"

    async def test_put_api_key_releases(self, db):
        """Saving an API key releases held events."""
        await _held_event(db)
        async with _client() as client:
            resp = await client.put("/settings/api-key", json={"provider": "google", "api_key": "k"})
        assert resp.json()["status"] == "stored"
        assert (await _status(db))[0] == "queued"

    async def test_update_custom_provider_releases(self, db):
        """Updating a custom provider releases held events."""
        await _held_event(db)
        providers = {"custom_providers": [
            {"id": "lm", "name": "LM", "base_url": "http://localhost:1234", "provider_type": "lmstudio"}
        ]}
        with patch("laya.api.settings_api.load_settings", return_value=providers), \
             patch("laya.api.settings_api.save_settings"):
            async with _client() as client:
                resp = await client.put(
                    "/settings/custom-providers/lm", json={"base_url": "http://localhost:4321"}
                )
        assert resp.status_code == 200
        assert (await _status(db))[0] == "queued"

    async def test_update_space_releases_on_model_fields_only(self, db):
        """A space model change releases held events; a rename doesn't."""
        await _held_event(db)
        async with _client() as client:
            await client.put("/spaces/default", json={"description": "renamed"})
            assert (await _status(db))[0] == "held"
            await client.put("/spaces/default", json={"router_model": "gemini/gemini-3.6-flash"})
        assert (await _status(db))[0] == "queued"

    async def test_save_space_api_key_releases(self, db):
        """Saving a space API key releases held events."""
        await _held_event(db)
        async with _client() as client:
            resp = await client.put(
                "/spaces/default/api-key", json={"provider": "gemini", "api_key": "k"}
            )
        assert resp.status_code == 200
        assert (await _status(db))[0] == "queued"


@pytest.mark.asyncio
class TestHealthModels:
    async def test_health_reports_models(self, db):
        """GET /health includes refusals and the held count."""
        await _held_event(db)
        async with _client() as client:
            models = (await client.get("/health")).json()["models"]
        assert models["held_events"] == 1
        assert models["unavailable"][0]["model"] == "gemini/gemini-2.0-flash"

    async def test_health_survives_models_status_error(self, db, monkeypatch):
        """GET /health returns 200 with models: null when status fails."""
        async def broken():
            raise RuntimeError("db gone")

        monkeypatch.setattr(model_availability, "status", broken)
        async with _client() as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["models"] is None
