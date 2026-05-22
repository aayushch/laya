# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the HTTP/SSE MCP transport: scope filtering, bearer auth, and
token management endpoints.

These tests build a minimal FastAPI app with just the MCP router/transport so
they don't need the full engine lifespan.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from laya.api.mcp_api import (
    ensure_startup_token,
    register_mcp_transport,
    router as mcp_router,
)
from laya.config import load_settings, save_settings
from laya.mcp.scope import enabled_tool_names, scope_of
from laya.security.keychain import (
    delete_mcp_token,
    get_mcp_token,
    store_mcp_token,
)


@pytest.fixture
def restore_settings():
    """Snapshot and restore mcp settings + bearer token around each test."""
    original = load_settings().get("mcp")
    yield
    s = load_settings()
    if original is None:
        s.pop("mcp", None)
    else:
        s["mcp"] = original
    save_settings(s)
    delete_mcp_token()


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_router)
    register_mcp_transport(app)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# scope helper
# ---------------------------------------------------------------------------


class TestScopeHelper:
    def test_all_off_returns_empty(self):
        assert enabled_tool_names({}) == set()
        assert enabled_tool_names({"read": False, "write": False, "egress": False}) == set()

    def test_read_only(self):
        names = enabled_tool_names({"read": True})
        assert "search_cards" in names
        assert "semantic_search" in names
        assert "get_settings" in names  # introspection counts as read
        assert "dismiss_card" not in names
        assert "update_theme" not in names

    def test_write_only(self):
        names = enabled_tool_names({"write": True})
        assert "dismiss_card" in names
        assert "update_theme" in names
        assert "search_cards" not in names

    def test_all_on_covers_union(self):
        names = enabled_tool_names({"read": True, "write": True, "egress": True})
        # All known categories present; egress includes per-platform tools
        assert "search_cards" in names
        assert "dismiss_card" in names
        assert len(names) >= 21  # 12 read + 9 write + at least some egress

    def test_scope_of_known_tools(self):
        assert scope_of("search_cards") == "read"
        assert scope_of("get_settings") == "read"
        assert scope_of("dismiss_card") == "write"
        assert scope_of("update_theme") == "write"
        assert scope_of("nonexistent_tool_xyz") is None


# ---------------------------------------------------------------------------
# /mcp/config and /mcp/token/*
# ---------------------------------------------------------------------------


class TestMcpConfigAndToken:
    def test_get_config_returns_defaults(self, client: TestClient, restore_settings):
        delete_mcp_token()
        r = client.get("/mcp/config")
        assert r.status_code == 200
        body = r.json()
        assert "tool_scopes" in body
        assert body["auth_mode"] in ("bearer", "none")
        assert body["has_token"] is False
        assert body["token_prefix"] is None
        assert body["sse_url"].endswith("/mcp/sse")

    def test_put_scopes_persists(self, client: TestClient, restore_settings):
        r = client.put(
            "/mcp/config",
            json={"tool_scopes": {"read": True, "write": True, "egress": False}},
        )
        assert r.status_code == 200
        assert r.json()["tool_scopes"] == {"read": True, "write": True, "egress": False}

        # Persisted in settings.json
        scopes = load_settings()["mcp"]["tool_scopes"]
        assert scopes["write"] is True

    def test_put_auth_bearer_autogenerates_token(self, client: TestClient, restore_settings):
        delete_mcp_token()
        r = client.put("/mcp/config", json={"auth_mode": "bearer"})
        assert r.status_code == 200
        assert r.json()["has_token"] is True

    def test_put_auth_invalid_rejected(self, client: TestClient, restore_settings):
        r = client.put("/mcp/config", json={"auth_mode": "garbage"})
        assert r.status_code == 400

    def test_token_refresh_rotates(self, client: TestClient, restore_settings):
        store_mcp_token("lyat_original")
        r = client.post("/mcp/token/refresh")
        assert r.status_code == 200
        new_token = r.json()["token"]
        assert new_token != "lyat_original"
        assert new_token.startswith("lyat_")
        assert get_mcp_token() == new_token

    def test_token_reveal_404_when_missing(self, client: TestClient, restore_settings):
        delete_mcp_token()
        r = client.get("/mcp/token/reveal")
        assert r.status_code == 404

    def test_token_delete_removes(self, client: TestClient, restore_settings):
        store_mcp_token("lyat_to_delete")
        r = client.delete("/mcp/token")
        assert r.status_code == 200
        assert get_mcp_token() is None


# ---------------------------------------------------------------------------
# Bearer auth on transport endpoints
# ---------------------------------------------------------------------------


class TestBearerAuth:
    def _enable_bearer(self) -> str:
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "bearer"
        save_settings(s)
        store_mcp_token("lyat_known_token_for_test")
        return "lyat_known_token_for_test"

    def _disable_auth(self):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "none"
        save_settings(s)

    def test_sse_rejects_when_no_header_in_bearer_mode(
        self, client: TestClient, restore_settings
    ):
        self._enable_bearer()
        r = client.get("/mcp/sse")
        assert r.status_code == 401

    def test_sse_rejects_wrong_token(self, client: TestClient, restore_settings):
        self._enable_bearer()
        r = client.get("/mcp/sse", headers={"Authorization": "Bearer wrong_token"})
        assert r.status_code == 401

    def test_sse_rejects_malformed_header(self, client: TestClient, restore_settings):
        self._enable_bearer()
        r = client.get("/mcp/sse", headers={"Authorization": "Token lyat_known_token_for_test"})
        assert r.status_code == 401

    def test_messages_rejects_when_no_header_in_bearer_mode(
        self, client: TestClient, restore_settings
    ):
        self._enable_bearer()
        r = client.post("/mcp/messages/?session_id=00000000000000000000000000000000")
        assert r.status_code == 401

    def test_messages_rejects_wrong_token(self, client: TestClient, restore_settings):
        self._enable_bearer()
        r = client.post(
            "/mcp/messages/?session_id=00000000000000000000000000000000",
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    def test_no_auth_bypass(self, client: TestClient, restore_settings):
        """When auth_mode=none, the endpoints don't require a token. We can't
        easily verify a successful SSE stream in the sync TestClient, but we
        can verify the auth check no longer rejects."""
        self._disable_auth()
        # POST without session_id → SSE transport returns 400, not 401, which
        # proves the auth layer was passed.
        r = client.post("/mcp/messages/")
        assert r.status_code != 401

    def test_token_rotation_invalidates_old_token(
        self, client: TestClient, restore_settings
    ):
        original = self._enable_bearer()
        # New token issued, old one is no longer valid via the bearer check.
        r = client.post("/mcp/token/refresh")
        new_token = r.json()["token"]
        assert new_token != original

        r = client.post(
            "/mcp/messages/?session_id=00000000000000000000000000000000",
            headers={"Authorization": f"Bearer {original}"},
        )
        assert r.status_code == 401

        r = client.post(
            "/mcp/messages/?session_id=00000000000000000000000000000000",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        # Auth passes; session is invalid so we get the SSE-layer's 404 for
        # an unknown session (not 401).
        assert r.status_code != 401


# ---------------------------------------------------------------------------
# Startup token bootstrap
# ---------------------------------------------------------------------------


class TestStartupToken:
    def test_ensure_startup_token_creates_when_missing(self, restore_settings):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "bearer"
        save_settings(s)
        delete_mcp_token()
        ensure_startup_token()
        assert get_mcp_token() is not None
        assert get_mcp_token().startswith("lyat_")

    def test_ensure_startup_token_preserves_existing(self, restore_settings):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "bearer"
        save_settings(s)
        store_mcp_token("lyat_preexisting")
        ensure_startup_token()
        assert get_mcp_token() == "lyat_preexisting"

    def test_ensure_startup_token_noop_when_auth_none(self, restore_settings):
        s = load_settings()
        s.setdefault("mcp", {})["auth_mode"] = "none"
        save_settings(s)
        delete_mcp_token()
        ensure_startup_token()
        assert get_mcp_token() is None
