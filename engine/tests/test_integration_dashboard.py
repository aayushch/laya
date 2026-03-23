"""Integration tests for the dashboard API."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


async def _seed_full_dashboard(db):
    """Seed a full set of data for dashboard testing."""
    # Events with space_id, processed, filtered columns
    for i in range(5):
        await db.execute(
            """INSERT INTO events
               (event_id, timestamp, source_platform, source_raw_event_type,
                subject_type, subject_id, subject_title, raw_json,
                processed, filtered, space_id)
               VALUES (?, datetime('now', ?), ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"evt_dash_{i}", f"-{i} hours",
             ["jira", "slack", "jira", "bitbucket", "gmail"][i],
             "issue_assigned", "ticket", f"BUG-{i}", f"Test event {i}", "{}",
             True, i == 4, None),  # Last event is filtered
        )

    # Cards with current status values (done, dismissed, ready)
    statuses = ["done", "dismissed", "ready", "done"]
    personas = ["ENGINEER", "COMMS", "ENGINEER", "PLANNER"]
    for i in range(4):
        entity_id = f"jira:ticket:BUG-{i}"
        await db.execute(
            """INSERT INTO action_cards
               (card_id, event_id, priority, persona, category,
                header, summary, status, privacy_tier, resolved_at,
                user_feedback, entity_id, space_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"card_dash_{i}", f"evt_dash_{i}", "HIGH", personas[i], "CODE",
             f"Card {i}", "Summary", statuses[i], 1,
             "2026-02-22T15:00:00Z" if statuses[i] in ("done", "dismissed") else None,
             "helpful" if statuses[i] == "done" else None,
             entity_id, None),
        )

    # Audit log entries
    for i in range(3):
        await db.execute(
            """INSERT INTO audit_log
               (log_id, step, model_used, input_tokens, output_tokens,
                latency_ms, success, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', ?))""",
            (f"audit_dash_{i}", "route", "anthropic/claude-haiku-4-5-20251001",
             500, 200, 150 + i * 50, True, f"-{i} hours"),
        )

    # Action log entries
    await db.execute(
        """INSERT INTO action_log
           (action_id, card_id, action_type, target_platform,
            payload, executed_at, result_status)
           VALUES (?, ?, ?, ?, ?, datetime('now'), ?)""",
        ("act_dash_0", "card_dash_0", "comment", "jira", '{}', "completed"),
    )

    await db.commit()


@pytest.mark.asyncio
class TestDashboardIntegration:
    """Integration tests for the dashboard endpoint."""

    async def test_full_seeded_data(self, db):
        """Dashboard with seeded data returns correct aggregate stats."""
        from laya.main import app

        await _seed_full_dashboard(db)

        with patch("laya.api.dashboard_api.get_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/dashboard?days=30")

        assert resp.status_code == 200
        data = resp.json()

        # Verify stats
        stats = data["stats"]
        assert stats["events_processed"] >= 4  # 4 processed (1 filtered)
        assert stats["cards_generated"] >= 4
        # cards_approved now counts 'done' + 'ready' statuses
        assert stats["cards_approved"] >= 2  # 2 done
        assert stats["cards_dismissed"] >= 1

    async def test_cost_accuracy(self, db):
        """LLM costs are computed from known token counts."""
        from laya.main import app

        # Insert audit entries with known token counts
        await db.execute(
            """INSERT INTO audit_log
               (log_id, step, model_used, input_tokens, output_tokens,
                latency_ms, success, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("audit_cost_1", "route", "anthropic/claude-haiku-4-5-20251001",
             1000, 500, 100, True),
        )
        await db.commit()

        with patch("laya.api.dashboard_api.get_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/dashboard?days=30")

        data = resp.json()
        assert data["llm_costs"]["total_cost_usd"] > 0

    async def test_period_filtering(self, db):
        """Events outside the period window are excluded."""
        from laya.main import app

        # Insert an event well outside 7-day window
        await db.execute(
            """INSERT INTO events
               (event_id, timestamp, source_platform, source_raw_event_type,
                subject_type, subject_id, raw_json, processed, filtered, space_id)
               VALUES (?, datetime('now', '-30 days'), ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("evt_old", "jira", "issue_assigned", "ticket", "BUG-99", "{}",
             True, False, None),
        )
        await db.commit()

        with patch("laya.api.dashboard_api.get_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/dashboard?days=7")

        data = resp.json()
        assert data["period_days"] == 7
        # The old event should not appear in the 7-day window
        assert data["stats"]["events_processed"] == 0

    async def test_empty_to_populated(self, db):
        """Dashboard goes from empty (zeros) to populated after seeding."""
        from laya.main import app

        with patch("laya.api.dashboard_api.get_db", return_value=db):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Empty dashboard
                resp1 = await client.get("/dashboard")
                data1 = resp1.json()
                assert data1["stats"]["events_processed"] == 0
                assert data1["stats"]["cards_generated"] == 0

            # Seed data
            await _seed_full_dashboard(db)

            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Populated dashboard
                resp2 = await client.get("/dashboard")
                data2 = resp2.json()
                assert data2["stats"]["events_processed"] > 0
                assert data2["stats"]["cards_generated"] > 0
