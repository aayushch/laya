"""Tests for the Dashboard REST API."""

import json

import pytest
from httpx import ASGITransport, AsyncClient


async def _insert_event(db, event_id, platform="jira", event_type="issue_assigned",
                        processed=True, filtered=False):
    await db.execute(
        "INSERT INTO events (event_id, timestamp, source_platform, source_raw_event_type, "
        "subject_type, subject_id, subject_title, raw_json, processed, filtered) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, "2026-02-22T14:30:00Z", platform, event_type,
         "ticket", "BUG-1", "Test", "{}", processed, filtered),
    )
    await db.commit()


async def _insert_card(db, card_id, event_id, status="pending", persona="ENGINEER",
                       priority="HIGH", user_feedback=None):
    await db.execute(
        "INSERT INTO action_cards (card_id, event_id, priority, persona, category, "
        "header, summary, status, privacy_tier, user_feedback, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (card_id, event_id, priority, persona, "CODE", "Header", "Summary",
         status, 1, user_feedback,
         "2026-02-22T15:00:00Z" if status in ("approved", "dismissed", "completed") else None),
    )
    await db.commit()


async def _insert_audit_entry(db, log_id, step="route", model="anthropic/claude-haiku-4-5-20251001",
                              input_tokens=500, output_tokens=200, latency_ms=450, success=True):
    await db.execute(
        "INSERT INTO audit_log (log_id, step, model_used, input_tokens, output_tokens, "
        "latency_ms, success) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (log_id, step, model, input_tokens, output_tokens, latency_ms, success),
    )
    await db.commit()


async def _insert_action_log(db, action_id, card_id, action_type="comment", result_status="completed"):
    await db.execute(
        "INSERT INTO action_log (action_id, card_id, action_type, target_platform, "
        "payload, result_status) VALUES (?, ?, ?, ?, ?, ?)",
        (action_id, card_id, action_type, "jira", "{}", result_status),
    )
    await db.commit()


@pytest.mark.asyncio
class TestDashboardAPI:
    async def test_empty_dashboard(self, db_m7):
        """GET /dashboard returns zeros when no data exists."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["events_processed"] == 0
        assert data["stats"]["cards_generated"] == 0
        assert data["stats"]["actions_executed"] == 0
        assert data["time_saved"]["total_minutes"] == 0
        assert data["llm_costs"]["total_cost_usd"] == 0
        assert data["period_days"] == 30

    async def test_event_counts(self, db_m7):
        """Dashboard correctly counts processed and filtered events."""
        await _insert_event(db_m7, "evt_1", processed=True, filtered=False)
        await _insert_event(db_m7, "evt_2", processed=True, filtered=True)
        await _insert_event(db_m7, "evt_3", processed=True, filtered=False)

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        assert data["stats"]["events_processed"] == 3
        assert data["stats"]["events_filtered"] == 1

    async def test_card_status_counts(self, db_m7):
        """Dashboard correctly counts cards by status."""
        await _insert_event(db_m7, "evt_cs_1")
        await _insert_event(db_m7, "evt_cs_2")
        await _insert_event(db_m7, "evt_cs_3")
        await _insert_card(db_m7, "card_1", "evt_cs_1", status="pending")
        await _insert_card(db_m7, "card_2", "evt_cs_2", status="approved")
        await _insert_card(db_m7, "card_3", "evt_cs_3", status="dismissed")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        assert data["stats"]["cards_generated"] == 3
        assert data["stats"]["cards_pending"] == 1
        assert data["stats"]["cards_approved"] == 1
        assert data["stats"]["cards_dismissed"] == 1

    async def test_llm_cost_calculation(self, db_m7):
        """Dashboard correctly calculates LLM costs from audit log."""
        await _insert_audit_entry(
            db_m7, "audit_1", model="anthropic/claude-haiku-4-5-20251001",
            input_tokens=1_000_000, output_tokens=500_000,
        )

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        assert data["llm_costs"]["total_input_tokens"] == 1_000_000
        assert data["llm_costs"]["total_output_tokens"] == 500_000
        assert data["llm_costs"]["total_cost_usd"] > 0
        assert "anthropic/claude-haiku-4-5-20251001" in data["llm_costs"]["by_model"]

    async def test_events_by_source(self, db_m7):
        """Dashboard groups events by source platform."""
        await _insert_event(db_m7, "evt_s1", platform="jira")
        await _insert_event(db_m7, "evt_s2", platform="jira")
        await _insert_event(db_m7, "evt_s3", platform="github")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        sources = {s["source"]: s["count"] for s in data["events_by_source"]}
        assert sources.get("jira") == 2
        assert sources.get("github") == 1

    async def test_approval_by_persona(self, db_m7):
        """Dashboard computes approval rates per persona."""
        await _insert_event(db_m7, "evt_p1")
        await _insert_event(db_m7, "evt_p2")
        await _insert_event(db_m7, "evt_p3")
        await _insert_card(db_m7, "card_p1", "evt_p1", persona="ENGINEER", status="approved")
        await _insert_card(db_m7, "card_p2", "evt_p2", persona="ENGINEER", status="dismissed")
        await _insert_card(db_m7, "card_p3", "evt_p3", persona="COMMS", status="approved")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        personas = {p["persona"]: p for p in data["approval_by_persona"]}
        assert personas["ENGINEER"]["approved"] == 1
        assert personas["ENGINEER"]["dismissed"] == 1
        assert personas["ENGINEER"]["rate"] == 0.5
        assert personas["COMMS"]["approved"] == 1

    async def test_response_time_stats(self, db_m7):
        """Dashboard computes response time percentiles from audit log."""
        for i, latency in enumerate([100, 200, 300, 400, 500]):
            await _insert_audit_entry(
                db_m7, f"audit_rt_{i}", step="route", latency_ms=latency,
            )

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        assert data["response_time"]["avg_ms"] == 300.0
        assert data["response_time"]["p50_ms"] == 300.0
        assert data["response_time"]["p95_ms"] >= 400.0

    async def test_time_saved(self, db_m7):
        """Dashboard estimates time saved from completed actions."""
        await _insert_event(db_m7, "evt_ts")
        await _insert_card(db_m7, "card_ts", "evt_ts", status="approved")
        await _insert_action_log(db_m7, "alog_1", "card_ts", action_type="comment", result_status="completed")
        await _insert_action_log(db_m7, "alog_2", "card_ts", action_type="code_fix", result_status="completed")

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        # comment=3min + code_fix=15min = 18min
        assert data["time_saved"]["total_minutes"] == 18.0
        assert data["time_saved"]["by_action_type"]["comment"] == 3.0
        assert data["time_saved"]["by_action_type"]["code_fix"] == 15.0

    async def test_days_parameter(self, db_m7):
        """Dashboard accepts custom days parameter."""
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard?days=7")

        assert resp.status_code == 200
        assert resp.json()["period_days"] == 7

    async def test_unknown_model_pricing(self, db_m7):
        """Dashboard uses fallback pricing for unknown models."""
        await _insert_audit_entry(
            db_m7, "audit_unk", model="unknown/model",
            input_tokens=1000, output_tokens=500,
        )

        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/dashboard")

        data = resp.json()
        assert data["llm_costs"]["total_cost_usd"] > 0
        assert "unknown/model" in data["llm_costs"]["by_model"]
