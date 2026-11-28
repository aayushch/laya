# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Trello boards to monitor, stored per ingestion clone like Slack channels."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from laya.egress.models import ConnectionResult

BOARD_ID = "5f0c1d2e3a4b5c6d7e8f9012"


def _client() -> AsyncClient:
    from laya.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _add_clone(db, connection_id: str, platform: str = "trello", workflow_id: str = "wf_trello") -> None:
    await db.execute(
        """INSERT INTO egress_connections (connection_id, platform, name, created_at, updated_at)
           VALUES (?, ?, ?, '2026-11-28', '2026-11-28')""",
        (connection_id, platform, f"{platform} account"),
    )
    await db.execute(
        """INSERT INTO sources (source_id, name, platform, workflow_id, source_type, connection_id)
           VALUES (?, ?, ?, ?, 'ingestion', ?)""",
        (f"src_{connection_id}", f"{platform} ingestion", platform, workflow_id, connection_id),
    )
    await db.commit()


async def _stored_boards(db, workflow_id: str = "wf_trello"):
    rows = await db.execute_fetchall(
        "SELECT value FROM metadata WHERE key = ? AND space_id = 'default'",
        (f"trello-boards:{workflow_id}",),
    )
    return json.loads(rows[0]["value"]) if rows else None


@pytest.mark.asyncio
async def test_create_stores_the_boards_for_the_ingestion_clone(db):
    async def connect(**kwargs):
        await _add_clone(db, "conn_trello")
        return ConnectionResult(status="connected", connection_id="conn_trello")

    with patch("laya.egress.connect", new=AsyncMock(side_effect=connect)) as connect_mock:
        async with _client() as client:
            resp = await client.post("/egress/connections", json={
                "platform": "trello",
                "name": "Work boards",
                "credentials": {"apiKey": "key", "apiToken": "token"},
                "boards": [" https://trello.com/b/AbCd1234/example-board ", BOARD_ID, "AbCd1234"],
            })

    assert resp.status_code == 200, resp.text
    assert connect_mock.await_args.kwargs["credentials"] == {"apiKey": "key", "apiToken": "token"}
    assert await _stored_boards(db) == ["AbCd1234", BOARD_ID]


@pytest.mark.asyncio
@pytest.mark.parametrize("boards", [None, [], ["https://trello.com/c/AbCd1234/a-card"], ["not a board"]])
async def test_create_refuses_missing_or_invalid_boards_before_connecting(db, boards):
    body = {"platform": "trello", "credentials": {"apiKey": "key", "apiToken": "token"}}
    if boards is not None:
        body["boards"] = boards

    with patch("laya.egress.connect", new=AsyncMock()) as connect_mock:
        async with _client() as client:
            resp = await client.post("/egress/connections", json=body)

    assert resp.status_code == 400
    connect_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_boards_can_be_read_and_replaced(db):
    await _add_clone(db, "conn_trello")

    async with _client() as client:
        assert (await client.get("/egress/connections/conn_trello/boards")).json() == {"boards": []}

        resp = await client.put("/egress/connections/conn_trello/boards", json={
            "boards": ["https://trello.com/b/XyZa9876", BOARD_ID],
        })
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"boards": ["XyZa9876", BOARD_ID]}
        assert (await client.get("/egress/connections/conn_trello/boards")).json() == {"boards": ["XyZa9876", BOARD_ID]}

        invalid = await client.put("/egress/connections/conn_trello/boards", json={"boards": ["nope"]})
        assert invalid.status_code == 400

    assert await _stored_boards(db) == ["XyZa9876", BOARD_ID]


@pytest.mark.asyncio
async def test_boards_belong_to_trello_connections_only(db):
    await _add_clone(db, "conn_slack", platform="slack", workflow_id="wf_slack")

    async with _client() as client:
        assert (await client.get("/egress/connections/conn_slack/boards")).status_code == 400
        assert (await client.put("/egress/connections/conn_slack/boards", json={"boards": ["AbCd1234"]})).status_code == 400
        assert (await client.get("/egress/connections/conn_missing/boards")).status_code == 404


@pytest.mark.asyncio
async def test_removing_the_connection_drops_its_boards(db):
    from laya.egress.connections import _remove_connection_workflows

    await _add_clone(db, "conn_trello")
    await db.execute(
        "INSERT INTO metadata (key, value, space_id) VALUES ('trello-boards:wf_trello', '[\"AbCd1234\"]', 'default')"
    )
    await db.commit()

    with patch("laya.security.keychain.get_api_key", return_value=None):
        await _remove_connection_workflows("conn_trello")

    assert await _stored_boards(db) is None
