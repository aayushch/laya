# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the OAuth proxy: auth-URL construction, token exchange and refresh.

Slack apps with PKCE enabled are public clients: Slack rejects a localhost
redirect without a PKCE challenge (issue #42) and authenticates the token
exchange with ``code_verifier`` instead of ``client_secret``. Apps without PKCE
still use a secret, so the flow has to work with and without one.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from laya.egress import oauth
from laya.egress.oauth import build_auth_url, handle_callback, refresh_access_token

REDIRECT = "http://localhost:8420/egress/connections/oauth/callback"


def _params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _mock_http(status_code: int = 200, body: dict | None = None):
    """Patch httpx.AsyncClient so post() records its kwargs and returns ``body``."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    resp.text = json.dumps(body or {})
    client.post = AsyncMock(return_value=resp)
    cls = MagicMock(return_value=client)
    return cls, client


class TestBuildAuthUrl:
    def test_slack_sends_pkce_challenge_with_user_scopes(self):
        with patch.object(oauth, "_get_oauth_client", return_value=("cid", "")):
            result = build_auth_url("slack", REDIRECT)

        assert "auth_url" in result
        params = _params(result["auth_url"])
        assert params["client_id"] == "cid"
        assert params["redirect_uri"] == REDIRECT
        assert "channels:history" in params["user_scope"]
        assert "scope" not in params  # bot scopes are empty
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"]
        # The verifier that matches this challenge is kept for the callback.
        state = oauth._oauth_states[result["state"]]
        assert state["platform"] == "slack"
        assert state["code_verifier"]

    def test_google_keeps_offline_consent_and_pkce(self):
        with patch.object(oauth, "_get_oauth_client", return_value=("cid", "sec")):
            result = build_auth_url("gmail", REDIRECT)

        params = _params(result["auth_url"])
        assert params["scope"] == "https://www.googleapis.com/auth/gmail.modify"
        assert params["access_type"] == "offline"
        assert params["prompt"] == "consent"
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"]

    def test_unconfigured_client_reports_needs_setup(self):
        with patch.object(oauth, "_get_oauth_client", return_value=None):
            result = build_auth_url("slack", REDIRECT)
        assert result.get("needs_setup") is True


class TestHandleCallbackTokenExchange:
    """The token request's shape depends on provider type and stored secret."""

    SLACK_TOKENS = {
        "ok": True,
        "authed_user": {
            "id": "U1",
            "access_token": "xoxp-user",
            "token_type": "user",
            "scope": "chat:write",
        },
    }
    GOOGLE_TOKENS = {
        "access_token": "ya29.x",
        "refresh_token": "1//r",
        "expires_in": 3600,
    }

    async def _run(self, db, platform: str, secret: str, tokens: dict) -> dict:
        with patch.object(oauth, "_get_oauth_client", return_value=("cid", secret)):
            start = build_auth_url(platform, REDIRECT)
        http_cls, http_client = _mock_http(200, tokens)
        with (
            patch.object(oauth, "_get_oauth_client", return_value=("cid", secret)),
            patch.object(oauth.httpx, "AsyncClient", http_cls),
            patch.object(oauth, "_store_in_keychain"),
            patch.object(oauth, "_provision_oauth_to_n8n", AsyncMock(return_value="cred1")),
            patch(
                "laya.egress.connections._clone_workflows_for_connection",
                AsyncMock(return_value=(1, [])),
            ),
        ):
            result = await handle_callback("the-code", start["state"], REDIRECT)
        assert result["status"] == "connected", result
        return http_client.post.call_args.kwargs["data"]

    @pytest.mark.asyncio
    async def test_slack_with_stored_secret_sends_secret_and_verifier(self, db):
        data = await self._run(db, "slack", "shh", self.SLACK_TOKENS)
        assert data["client_secret"] == "shh"
        assert data["code_verifier"]
        assert data["redirect_uri"] == REDIRECT

    @pytest.mark.asyncio
    async def test_slack_without_secret_sends_verifier_only(self, db):
        data = await self._run(db, "slack", "", self.SLACK_TOKENS)
        assert "client_secret" not in data
        assert data["code_verifier"]
        assert data["client_id"] == "cid"

    @pytest.mark.asyncio
    async def test_google_always_sends_secret_and_verifier(self, db):
        data = await self._run(db, "gmail", "shh", self.GOOGLE_TOKENS)
        assert data["client_secret"] == "shh"
        assert data["code_verifier"]

    @pytest.mark.asyncio
    async def test_slack_user_token_is_lifted_to_top_level(self, db):
        with patch.object(oauth, "_store_in_keychain") as store:
            await self._run_with_store(db, store)
        token_store = store.call_args.args[2]
        assert token_store["access_token"] == "xoxp-user"
        assert token_store["client_secret"] == ""

    async def _run_with_store(self, db, store) -> None:
        with patch.object(oauth, "_get_oauth_client", return_value=("cid", "")):
            start = build_auth_url("slack", REDIRECT)
        http_cls, _ = _mock_http(200, self.SLACK_TOKENS)
        with (
            patch.object(oauth, "_get_oauth_client", return_value=("cid", "")),
            patch.object(oauth.httpx, "AsyncClient", http_cls),
            patch.object(oauth, "_store_in_keychain", store),
            patch.object(oauth, "_provision_oauth_to_n8n", AsyncMock(return_value="cred1")),
            patch(
                "laya.egress.connections._clone_workflows_for_connection",
                AsyncMock(return_value=(1, [])),
            ),
        ):
            await handle_callback("the-code", start["state"], REDIRECT)


class TestRefreshAccessToken:
    def _keyring(self, token_store: dict):
        keyring = MagicMock()
        keyring.get_password = MagicMock(return_value=json.dumps(token_store))
        return keyring

    async def _refresh(self, db, platform: str, token_store: dict):
        http_cls, http_client = _mock_http(200, {"access_token": "new", "refresh_token": "r2"})
        with (
            patch.dict("sys.modules", {"keyring": self._keyring(token_store)}),
            patch.object(oauth.httpx, "AsyncClient", http_cls),
            patch.object(oauth, "_store_in_keychain"),
        ):
            ok = await refresh_access_token("conn_1", platform)
        return ok, http_client

    @pytest.mark.asyncio
    async def test_slack_refreshes_without_secret(self, db):
        ok, http_client = await self._refresh(db, "slack", {
            "refresh_token": "r1", "client_id": "cid", "client_secret": "",
        })
        assert ok is True
        data = http_client.post.call_args.kwargs["data"]
        assert "client_secret" not in data
        assert data["grant_type"] == "refresh_token"

    @pytest.mark.asyncio
    async def test_google_refresh_requires_secret(self, db):
        ok, http_client = await self._refresh(db, "gmail", {
            "refresh_token": "r1", "client_id": "cid", "client_secret": "",
        })
        assert ok is False
        http_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_google_refresh_sends_secret(self, db):
        ok, http_client = await self._refresh(db, "gmail", {
            "refresh_token": "r1", "client_id": "cid", "client_secret": "shh",
        })
        assert ok is True
        assert http_client.post.call_args.kwargs["data"]["client_secret"] == "shh"
