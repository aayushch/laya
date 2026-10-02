# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for engine REST API authentication and rotation."""

import pytest
from httpx import ASGITransport, AsyncClient

from laya.main import app
from laya.security.keychain import (
    ensure_engine_token,
    get_engine_token,
    store_engine_token,
    store_mcp_token,
)


@pytest.fixture(autouse=True)
def setup_engine_token():
    """Ensure a clean, known engine token for each test."""
    test_token = "lyae_test_token_1234567890abcdef"
    store_engine_token(test_token)
    yield test_token


@pytest.mark.asyncio
async def test_no_authorization_returns_401():
    """No Authorization header -> 401 on protected REST route."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/settings")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}
    assert resp.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_wrong_token_returns_401():
    """Wrong token -> 401 on protected REST route."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/settings",
            headers={"Authorization": "Bearer completely_wrong_token"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}


@pytest.mark.asyncio
async def test_malformed_authorization_header_returns_401():
    """Malformed Authorization header (e.g. Basic instead of Bearer) -> 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/settings",
            headers={"Authorization": "Basic 12345"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_succeeds(setup_engine_token):
    """Valid token -> request succeeds."""
    token = setup_engine_token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert "api_keys" in resp.json()


@pytest.mark.asyncio
async def test_health_exempt_from_auth():
    """GET /health -> 200 without token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine"] == "healthy"


@pytest.mark.asyncio
async def test_events_interim_exemption(db):
    """POST /events -> accepted without token (documented interim exemption for n8n)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/events",
            json={
                "event_id": "evt_n8n_test_auth",
                "timestamp": "2026-02-22T14:30:00Z",
                "source": {
                    "platform": "jira",
                    "raw_event_type": "issue_created",
                },
                "actor": {"name": "Bot", "email": "bot@example.com"},
                "subject": {"type": "ticket", "id": "TEST-1", "title": "Test"},
                "content": {"body": "Test body"},
            },
        )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_other_events_routes_require_auth():
    """GET /events/dead, GET /events/counts require engine auth even though POST /events is exempt."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/events/dead")
        assert resp.status_code == 401
        resp2 = await client.get("/events/counts")
        assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_repos_get_exempt_and_put_requires_auth():
    """GET /repos is exempt (used by n8n workflows); PUT /repos requires auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /repos without token succeeds
        resp = await client.get("/repos?platform=github")
        assert resp.status_code == 200

        # PUT /repos without token is rejected
        put_resp = await client.put("/repos", json={"repos": []})
        assert put_resp.status_code == 401


@pytest.mark.asyncio
async def test_metadata_exemptions_and_restrictions(db):
    """GET /metadata, GET/PUT /metadata/{key} are exempt for n8n; DELETE requires auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /metadata list without token
        resp = await client.get("/metadata")
        assert resp.status_code == 200

        # PUT /metadata/{key} without token (n8n state storage)
        put_resp = await client.put(
            "/metadata/slack-channels:wf_123",
            json={"value": {"channels": ["general"]}},
        )
        assert put_resp.status_code == 200

        # GET /metadata/{key} without token (n8n config fetch)
        get_resp = await client.get("/metadata/slack-channels:wf_123")
        assert get_resp.status_code == 200
        assert get_resp.json()["value"] == {"channels": ["general"]}

        # DELETE /metadata/{key} requires auth
        del_resp = await client.delete("/metadata/slack-channels:wf_123")
        assert del_resp.status_code == 401


@pytest.mark.asyncio
async def test_ingestion_errors_exemptions_and_restrictions(db):
    """POST /ingestion-errors is exempt (n8n error handler); GET and clear routes require auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # POST /ingestion-errors without token -> 202
        post_resp = await client.post(
            "/ingestion-errors",
            json={
                "workflow_id": "wf_test_123",
                "error_message": "Network timeout to source",
                "occurred_at": "2026-02-22T14:30:00Z",
            },
        )
        assert post_resp.status_code == 202

        # GET /ingestion-errors (UI audit/surfacing) -> 401
        get_resp = await client.get("/ingestion-errors")
        assert get_resp.status_code == 401

        # Clear routes -> 401
        clear_resp = await client.post("/ingestion-errors/clear-all")
        assert clear_resp.status_code == 401


@pytest.mark.asyncio
async def test_vendor_oauth_callback_exempt():
    """GET /egress/connections/oauth/callback is exempt from engine bearer auth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Calling without code/state returns 422 validation error, not 401 Unauthorized
        resp = await client.get("/egress/connections/oauth/callback")
        assert resp.status_code != 401


@pytest.mark.asyncio
async def test_mcp_reveal_requires_engine_auth(setup_engine_token):
    """GET /mcp/token/reveal is NOT callable anonymously (closes sharp vulnerability)."""
    store_mcp_token("lyat_secret_mcp_token")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Anonymous -> 401
        anon_resp = await client.get("/mcp/token/reveal")
        assert anon_resp.status_code == 401

        # With valid engine token -> 200
        auth_resp = await client.get(
            "/mcp/token/reveal",
            headers={"Authorization": f"Bearer {setup_engine_token}"},
        )
        assert auth_resp.status_code == 200
        assert auth_resp.json()["token"] == "lyat_secret_mcp_token"


@pytest.mark.asyncio
async def test_force_rotate_invalidates_old_token(setup_engine_token):
    """POST /auth/rotate replaces token; old token immediately returns 401, new token works."""
    old_token = setup_engine_token
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthenticated rotate -> 401
        anon_rotate = await client.post("/auth/rotate")
        assert anon_rotate.status_code == 401

        # Authenticated rotate -> 200 + new token
        rotate_resp = await client.post(
            "/auth/rotate",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert rotate_resp.status_code == 200
        new_token = rotate_resp.json()["token"]
        assert new_token != old_token
        assert get_engine_token() == new_token

        # Old token immediately fails on subsequent requests
        old_resp = await client.get(
            "/settings",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert old_resp.status_code == 401

        # New token succeeds
        new_resp = await client.get(
            "/settings",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert new_resp.status_code == 200


@pytest.mark.asyncio
async def test_in_process_cache_invalidated_on_rotate():
    """In-process cache is dropped and updated immediately on rotation."""
    t1 = ensure_engine_token()
    assert get_engine_token() == t1

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/auth/rotate",
            headers={"Authorization": f"Bearer {t1}"},
        )
        assert resp.status_code == 200
        t2 = resp.json()["token"]
        assert t2 != t1

        # Direct in-process retrieval returns new token, not cached t1
        assert get_engine_token() == t2
