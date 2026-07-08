# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Spaces REST API pause/resume n8n resilience, plus the n8n
client's transport-error wrapping that makes that resilience possible."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from laya.integrations.n8n_client import N8nApiError


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

    async def test_resume_survives_n8n_unavailable(self, db):
        """A slow/unreachable n8n must not 500 Resume. The n8n client wraps
        transport failures into a 503 N8nApiError, which the endpoint records
        per-workflow instead of letting it crash the request."""
        await self._setup(db)
        with patch("laya.api.spaces_api.check_workflow_readiness",
                   new=AsyncMock(return_value={"ready": True, "issues": []})), \
             patch("laya.api.spaces_api.activate_workflow",
                   new=AsyncMock(side_effect=N8nApiError(503, "n8n did not respond (ReadTimeout)"))):
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

    async def test_pause_survives_n8n_unavailable(self, db):
        await self._setup(db)
        with patch("laya.api.spaces_api.activate_workflow",
                   new=AsyncMock(side_effect=N8nApiError(503, "n8n did not respond (ConnectError)"))):
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


@pytest.mark.asyncio
class TestN8nClientTransportWrapping:
    """The n8n client funnels raw httpx transport failures into N8nApiError so
    that EVERY caller's `except N8nApiError` covers them — not just the handful
    that also happened to catch httpx.HTTPError (the Resume regression)."""

    def _client_raising(self, exc):
        """A stand-in for get_client() whose .request() raises `exc`."""
        fake = MagicMock()
        fake.request = AsyncMock(side_effect=exc)
        return fake

    @pytest.mark.parametrize("exc", [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("peer closed"),
    ])
    async def test_transport_errors_become_n8n_api_error(self, exc):
        from laya.integrations import n8n_client

        with patch.object(n8n_client, "get_client", return_value=self._client_raising(exc)), \
             patch.object(n8n_client, "_get_headers", return_value={}), \
             patch.object(n8n_client, "_base_url", return_value="http://n8n.test"):
            with pytest.raises(N8nApiError) as ei:
                await n8n_client.activate_workflow("wf_1", active=True)

        # 503 keeps HTTPException(status_code=e.status_code) mappings valid, and
        # the failing transport type stays visible in the detail.
        assert ei.value.status_code == 503
        assert type(exc).__name__ in ei.value.detail

    async def test_non_2xx_response_still_wrapped_with_real_status(self):
        """A genuine HTTP error response keeps its own status code — the
        transport wrapper only fires when there is NO response."""
        from laya.integrations import n8n_client

        resp = httpx.Response(409, text="conflict")
        fake = MagicMock()
        fake.request = AsyncMock(return_value=resp)
        with patch.object(n8n_client, "get_client", return_value=fake), \
             patch.object(n8n_client, "_get_headers", return_value={}), \
             patch.object(n8n_client, "_base_url", return_value="http://n8n.test"):
            with pytest.raises(N8nApiError) as ei:
                await n8n_client.activate_workflow("wf_1", active=True)

        assert ei.value.status_code == 409
