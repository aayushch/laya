# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Omni API — rolling cross-platform summary."""

import json

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
class TestOmniAPI:
    async def test_get_omni_empty(self, db):
        """GET /omni returns empty snapshot when nothing exists."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/omni")

        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id"] is None
        assert data["version"] == 0
        assert data["sections"] == []

    async def test_get_omni_with_snapshot(self, db):
        """GET /omni returns the latest snapshot."""
        content = {
            "sections": [
                {
                    "type": "attention",
                    "label": None,
                    "items": [
                        {
                            "text": "PR #412 needs review",
                            "source_cards": ["card_abc"],
                            "platforms": ["bitbucket"],
                            "priority": "HIGH",
                            "pinned": False,
                        }
                    ],
                }
            ],
            "stats": {"events_processed": 10, "cards_acted_on": 3, "compression_ratio": 0.4},
        }

        await db.execute(
            """INSERT INTO omni_snapshots
               (snapshot_id, space_id, version, generated_at, snapshot_type,
                content_json, card_ids, events_processed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "omni_test1",
                "default",
                1,
                "2026-04-07T17:00:00",
                "scheduled",
                json.dumps(content),
                json.dumps(["card_abc"]),
                10,
            ),
        )
        await db.commit()

        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/omni")

        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_id"] == "omni_test1"
        assert data["version"] == 1
        assert len(data["sections"]) == 1
        assert data["sections"][0]["type"] == "attention"
        assert data["sections"][0]["items"][0]["text"] == "PR #412 needs review"

    async def test_get_omni_specific_version(self, db):
        """GET /omni?version=1 returns the specific version."""
        for v in (1, 2):
            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"omni_v{v}",
                    "default",
                    v,
                    f"2026-04-0{v}T17:00:00",
                    "scheduled",
                    json.dumps({"sections": [], "stats": {}}),
                    "[]",
                    v * 5,
                ),
            )
        await db.commit()

        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/omni?version=1")

        data = resp.json()
        assert data["version"] == 1
        assert data["snapshot_id"] == "omni_v1"

    async def test_get_omni_history(self, db):
        """GET /omni/history returns snapshot list."""
        for v in (1, 2, 3):
            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"omni_h{v}",
                    "default",
                    v,
                    f"2026-04-0{v}T17:00:00",
                    "incremental" if v < 3 else "scheduled",
                    json.dumps({"sections": []}),
                    "[]",
                    v,
                ),
            )
        await db.commit()

        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/omni/history")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["snapshots"]) == 3
        # Latest first
        assert data["snapshots"][0]["version"] == 3

    async def test_pin_and_list_pins(self, db):
        """POST /omni/pin creates a pin, GET /omni/pins lists it."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create pin
            resp = await client.post(
                "/omni/pin",
                json={
                    "space_id": "default",
                    "text": "Auth migration is critical",
                    "source_cards": ["card_1", "card_2"],
                    "platforms": ["jira", "bitbucket"],
                },
            )

        assert resp.status_code == 200
        pin_data = resp.json()
        assert pin_data["item_text"] == "Auth migration is critical"
        assert pin_data["source_card_ids"] == ["card_1", "card_2"]
        pin_id = pin_data["pin_id"]

        # List pins
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/omni/pins")

        data = resp.json()
        assert len(data["pins"]) == 1
        assert data["pins"][0]["pin_id"] == pin_id

    async def test_unpin_item(self, db):
        """DELETE /omni/pin/:pin_id removes a pin."""
        # Insert a pin directly
        await db.execute(
            """INSERT INTO omni_pins
               (pin_id, space_id, item_text, source_card_ids, platforms, pinned_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("pin_del", "default", "Test pin", "[]", "[]", "2026-04-07T10:00:00"),
        )
        await db.commit()

        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/omni/pin/pin_del")

        assert resp.status_code == 200
        assert resp.json()["pin_id"] == "pin_del"

        # Verify deleted
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/omni/pins")

        assert len(resp.json()["pins"]) == 0

    async def test_unpin_nonexistent(self, db):
        """DELETE /omni/pin/:pin_id returns 404 for missing pin."""
        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/omni/pin/does_not_exist")

        assert resp.status_code == 404

    async def test_space_isolation(self, db):
        """Omni snapshots are isolated by space_id."""
        for sid in ("space_a", "space_b"):
            await db.execute(
                """INSERT INTO omni_snapshots
                   (snapshot_id, space_id, version, generated_at, snapshot_type,
                    content_json, card_ids, events_processed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"omni_{sid}",
                    sid,
                    1,
                    "2026-04-07T17:00:00",
                    "scheduled",
                    json.dumps({"sections": [], "stats": {}}),
                    "[]",
                    1,
                ),
            )
        await db.commit()

        from laya.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp_a = await client.get("/omni?space_id=space_a")
            resp_b = await client.get("/omni?space_id=space_b")

        assert resp_a.json()["snapshot_id"] == "omni_space_a"
        assert resp_b.json()["snapshot_id"] == "omni_space_b"
