# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Spaces REST API — pause/resume n8n resilience."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
class TestSetSpacePaused:
    async def _setup(self, db):
        await db.execute(
            "INSERT INTO spaces (space_id, name) VALUES (?, ?)", ("sp_1", "Test Space")
        )
        await db.execute(
            "INSERT INTO sources (source_id, name, platform, workflow_id, space_id, source_type) "
            "VALUES (?, ?, ?, ?, ?, 'ingestion')",
            ("src_1", "Jira Ingest", "jira", "wf_1", "sp_1"),
        )
        await db.commit()

    async def _put_paused(self, paused: bool):
        from laya.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.put("/spaces/sp_1/paused", json={"paused": paused})

    async def test_resume_survives_n8n_read_timeout(self, db):
        """A slow n8n (ReadTimeout on activate) must not 500 Resume — the client
        only wraps non-2xx responses in N8nApiError, so the raw transport error
        used to escape and crash the endpoint. Now it's reported per-workflow."""
        await self._setup(db)
        with patch("laya.api.spaces_api.check_workflow_readiness",
                   new=AsyncMock(return_value={"ready": True, "issues": []})), \
             patch("laya.api.spaces_api.activate_workflow",
                   new=AsyncMock(side_effect=httpx.ReadTimeout("timed out"))):
            resp = await self._put_paused(paused=False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["workflows_toggled"] == 0
        assert len(data["errors"]) == 1
        assert data["errors"][0]["workflow_id"] == "wf_1"
        # The paused flag still persists even though n8n didn't respond.
        rows = await db.execute_fetchall(
            "SELECT paused FROM spaces WHERE space_id = 'sp_1'"
        )
        assert rows[0]["paused"] == 0

    async def test_pause_survives_n8n_connect_error(self, db):
        await self._setup(db)
        with patch("laya.api.spaces_api.activate_workflow",
                   new=AsyncMock(side_effect=httpx.ConnectError("connection refused"))):
            resp = await self._put_paused(paused=True)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paused"
        assert len(data["errors"]) == 1
        rows = await db.execute_fetchall(
            "SELECT paused FROM spaces WHERE space_id = 'sp_1'"
        )
        assert rows[0]["paused"] == 1

    async def test_toggle_succeeds_when_n8n_ok(self, db):
        await self._setup(db)
        with patch("laya.api.spaces_api.check_workflow_readiness",
                   new=AsyncMock(return_value={"ready": True, "issues": []})), \
             patch("laya.api.spaces_api.activate_workflow",
                   new=AsyncMock(return_value={})):
            resp = await self._put_paused(paused=False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["workflows_toggled"] == 1
        assert data["errors"] == []
