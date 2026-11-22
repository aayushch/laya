# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for engine REST API authentication."""

import pytest
from httpx import ASGITransport, AsyncClient

from laya.main import app
from laya.security import keychain
from laya.security.keychain import (
    ENGINE_TOKEN_ENV,
    delete_engine_token,
    ensure_engine_token,
    get_engine_token,
    store_engine_token,
    store_mcp_token,
)


@pytest.fixture(autouse=True)
def setup_engine_token(monkeypatch):
    """Ensure a clean, known keychain engine token and no pinned token for each test."""
    monkeypatch.setattr(keychain, "_process_engine_token", None)
    monkeypatch.delenv(ENGINE_TOKEN_ENV, raising=False)
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
    assert data["token"] == "absent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "expected"),
    [
        ("Bearer {token}", "accepted"),
        ("Bearer token_of_an_earlier_launch", "rejected"),
        ("Basic 12345", "rejected"),
    ],
)
async def test_health_reports_whether_it_accepts_the_token(
    setup_engine_token, authorization, expected
):
    """GET /health stays 200 for any credential and says whether it accepts it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/health",
            headers={"Authorization": authorization.format(token=setup_engine_token)},
        )
    assert resp.status_code == 200
    assert resp.json()["token"] == expected


@pytest.mark.asyncio
async def test_sibling_routes_of_n8n_routes_require_the_app_token(db, setup_engine_token):
    """The n8n routes' siblings stay app-token only (the n8n route tests live in test_n8n_auth.py)."""
    auth = {"Authorization": f"Bearer {setup_engine_token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/events/dead")).status_code == 401
        assert (await client.get("/events/counts")).status_code == 401
        assert (await client.put("/repos", json={"repos": []})).status_code == 401
        assert (await client.get("/metadata")).status_code == 401
        anon_put = await client.put(
            "/metadata/slack-channels:wf_123", json={"value": {"channels": ["general"]}},
        )
        assert anon_put.status_code == 401
        put_resp = await client.put(
            "/metadata/slack-channels:wf_123",
            json={"value": {"channels": ["general"]}},
            headers=auth,
        )
        assert put_resp.status_code == 200
        assert (await client.delete("/metadata/slack-channels:wf_123")).status_code == 401
        assert (await client.get("/ingestion-errors")).status_code == 401
        assert (await client.post("/ingestion-errors/clear-all")).status_code == 401


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
async def test_non_ascii_bearer_returns_401():
    """A bearer with bytes >= 0x80 is rejected with 401, not a 500 from the comparison."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/settings",
            headers={"Authorization": "Bearer caf\xe9".encode("latin-1")},
        )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}


@pytest.mark.asyncio
async def test_env_token_wins_and_is_removed_from_environment(monkeypatch, setup_engine_token):
    """A launcher-supplied token replaces the keychain token and is not left in os.environ."""
    import os

    monkeypatch.setenv(ENGINE_TOKEN_ENV, "env_supplied_token")
    assert ensure_engine_token() == "env_supplied_token"
    assert ENGINE_TOKEN_ENV not in os.environ
    assert get_engine_token() == "env_supplied_token"
    # Resolving again keeps the pinned token even though the variable is gone.
    assert ensure_engine_token() == "env_supplied_token"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        keychain_resp = await client.get(
            "/settings", headers={"Authorization": f"Bearer {setup_engine_token}"}
        )
        env_resp = await client.get(
            "/settings", headers={"Authorization": "Bearer env_supplied_token"}
        )
    assert keychain_resp.status_code == 401
    assert env_resp.status_code == 200


def test_ensure_returns_existing_keychain_token(setup_engine_token):
    """Without the env var, the stored keychain token is used as is."""
    assert ensure_engine_token() == setup_engine_token


@pytest.mark.asyncio
async def test_minted_token_is_pinned_when_keychain_write_fails(monkeypatch):
    """With no usable keychain the minted token still authenticates for this process."""
    delete_engine_token()
    monkeypatch.setattr(keychain, "store_engine_token", lambda token: False)

    token = ensure_engine_token()
    assert token
    assert get_engine_token() == token

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/settings", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
