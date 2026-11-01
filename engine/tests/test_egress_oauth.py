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


class TestN8nCredentialSync:
    """After a refresh, n8n's OAuth credential gets the engine's client and tokens.

    n8n refreshes Google tokens itself with its own credential, so a stale
    client there breaks the ingestion workflows while the engine keeps working
    (issue #54). A failed update must show up in connection health.
    """

    @pytest.fixture(autouse=True)
    def _clear_sync_errors(self):
        oauth._n8n_sync_errors.clear()
        yield
        oauth._n8n_sync_errors.clear()

    async def _insert_connection(self, db, connection_id="conn_1", platform="gmail"):
        await db.execute(
            """INSERT INTO egress_connections
               (connection_id, platform, name, n8n_credential_id, status,
                capabilities, created_at, updated_at)
               VALUES (?, ?, 'G', 'cred_9', 'connected', '[]', '2026-01-01 00:00:00',
                       '2026-01-01 00:00:00')""",
            (connection_id, platform),
        )
        await db.commit()

    async def _refresh(self, update_credential, new_tokens):
        token_store = {"refresh_token": "r1", "client_id": "cid", "client_secret": "shh"}
        keyring = MagicMock()
        keyring.get_password = MagicMock(return_value=json.dumps(token_store))
        http_cls, _ = _mock_http(200, new_tokens)
        with (
            patch.dict("sys.modules", {"keyring": keyring}),
            patch.object(oauth.httpx, "AsyncClient", http_cls),
            patch.object(oauth, "_store_in_keychain"),
            patch("laya.integrations.n8n_client.update_credential", update_credential),
        ):
            return await refresh_access_token("conn_1", "gmail")

    @pytest.mark.asyncio
    async def test_refresh_patches_client_and_full_token_data(self, db):
        await self._insert_connection(db)
        update = AsyncMock(return_value={})
        ok = await self._refresh(update, {"access_token": "new", "expires_in": 3599})

        assert ok is True
        cred_id, data = update.call_args.args
        assert cred_id == "cred_9"
        assert data["clientId"] == "cid"
        assert data["clientSecret"] == "shh"
        token = data["oauthTokenData"]
        assert token["access_token"] == "new"
        assert token["expires_in"] == 3599
        # Google omits refresh_token on refresh; the stored one is kept.
        assert token["refresh_token"] == "r1"
        assert oauth.get_n8n_sync_error("conn_1") is None

    @pytest.mark.asyncio
    async def test_failed_update_is_recorded_and_cleared_on_success(self, db):
        from laya.integrations.n8n_client import N8nApiError

        await self._insert_connection(db)
        ok = await self._refresh(
            AsyncMock(side_effect=N8nApiError(403, "forbidden")), {"access_token": "new"},
        )
        assert ok is True
        assert "credential:update" in oauth.get_n8n_sync_error("conn_1")

        await self._refresh(AsyncMock(return_value={}), {"access_token": "newer"})
        assert oauth.get_n8n_sync_error("conn_1") is None

    @pytest.mark.asyncio
    async def test_health_marks_connection_error_on_sync_failure(self, db):
        from laya.egress import health

        await self._insert_connection(db)
        oauth._n8n_sync_errors["conn_1"] = "n8n credential update failed: boom"
        with (
            patch.object(health, "get_db", AsyncMock(return_value=db)),
            patch.object(health, "_check_oauth_token", AsyncMock(return_value=True)),
        ):
            await health._run_health_checks()

        rows = await db.execute_fetchall(
            "SELECT status, error_message FROM egress_connections WHERE connection_id = 'conn_1'"
        )
        assert rows[0]["status"] == "error"
        assert rows[0]["error_message"] == "n8n credential update failed: boom"


class TestN8nCredentialData:
    """n8n validates every credential write, partial updates included, against
    the type's full schema; these fields must be present or it returns 400."""

    SCHEMA_FIELDS = {"clientId", "clientSecret", "serverUrl",
                     "sendAdditionalBodyProperties", "additionalBodyProperties"}

    @pytest.mark.parametrize("platform", sorted(oauth.OAUTH_PROVIDERS))
    def test_every_provider_carries_schema_fields(self, platform):
        n8n_type = oauth.OAUTH_PROVIDERS[platform]["n8n_type"]
        data = oauth._n8n_credential_data(n8n_type, "cid", "sec", token_data={"access_token": "a"})
        assert self.SCHEMA_FIELDS <= data.keys()
        assert data["oauthTokenData"]["access_token"] == "a"
        if n8n_type == "microsoftOutlookOAuth2Api":
            assert "userPrincipalName" in data

    def test_outlook_keeps_graph_url_and_upn(self):
        data = oauth._n8n_credential_data(
            "microsoftOutlookOAuth2Api", "cid", "sec", user_email="me@live.in",
        )
        assert data["serverUrl"] == "https://graph.microsoft.com"
        assert data["userPrincipalName"] == "me@live.in"


class TestApplyClientToConnections:
    @pytest.mark.asyncio
    async def test_rotated_secret_reaches_keychain_and_n8n(self, db):
        await db.execute(
            """INSERT INTO egress_connections
               (connection_id, platform, name, n8n_credential_id, status,
                capabilities, created_at, updated_at)
               VALUES ('conn_1', 'gmail', 'G', 'cred_9', 'connected', '[]',
                       '2026-01-01 00:00:00', '2026-01-01 00:00:00')"""
        )
        await db.commit()
        store = MagicMock()
        update = AsyncMock(return_value={})
        with (
            patch.object(oauth, "get_db", AsyncMock(return_value=db)),
            patch.object(oauth, "_get_from_keychain", return_value={
                "refresh_token": "r1", "client_id": "cid", "client_secret": "old",
            }),
            patch.object(oauth, "_store_in_keychain", store),
            patch("laya.integrations.n8n_client.update_credential", update),
        ):
            await oauth.apply_client_to_connections("gmail", "cid", "new")

        assert store.call_args.args[2]["client_secret"] == "new"
        cred_id, data = update.call_args.args
        assert cred_id == "cred_9"
        assert data["clientId"] == "cid" and data["clientSecret"] == "new"
        # A client-only update leaves the stored tokens in n8n untouched.
        assert "oauthTokenData" not in data

    @pytest.mark.asyncio
    async def test_different_client_id_is_left_alone(self, db):
        await db.execute(
            """INSERT INTO egress_connections
               (connection_id, platform, name, n8n_credential_id, status,
                capabilities, created_at, updated_at)
               VALUES ('conn_1', 'gmail', 'G', 'cred_9', 'connected', '[]',
                       '2026-01-01 00:00:00', '2026-01-01 00:00:00')"""
        )
        await db.commit()
        store = MagicMock()
        update = AsyncMock()
        with (
            patch.object(oauth, "get_db", AsyncMock(return_value=db)),
            patch.object(oauth, "_get_from_keychain", return_value={
                "refresh_token": "r1", "client_id": "other", "client_secret": "old",
            }),
            patch.object(oauth, "_store_in_keychain", store),
            patch("laya.integrations.n8n_client.update_credential", update),
        ):
            await oauth.apply_client_to_connections("gmail", "cid", "new")

        store.assert_not_called()
        update.assert_not_called()


class TestReauthorizeInPlace:
    """Reconnect re-authorizes an existing connection instead of adding a new one."""

    @pytest.fixture(autouse=True)
    def _clear_sync_errors(self):
        oauth._n8n_sync_errors.clear()
        yield
        oauth._n8n_sync_errors.clear()

    async def _reconnect(self, db, update_credential, cred_id="cred_9"):
        await db.execute(
            """INSERT INTO egress_connections
               (connection_id, platform, name, n8n_credential_id, space_id, status,
                error_message, capabilities, created_at, updated_at)
               VALUES ('conn_1', 'gmail', 'Work', ?, 'default', 'error',
                       'invalid_grant', '["send_email"]',
                       '2026-01-01 00:00:00', '2026-01-01 00:00:00')""",
            (cred_id,),
        )
        await db.commit()
        with patch.object(oauth, "_get_oauth_client", return_value=("cid", "shh")):
            start = build_auth_url("gmail", REDIRECT, connection_id="conn_1")
        http_cls, _ = _mock_http(200, {"access_token": "ya29.new", "refresh_token": "1//r2"})
        store = MagicMock()
        provision = AsyncMock(return_value="cred_new")
        with (
            patch.object(oauth, "_get_oauth_client", return_value=("cid", "shh")),
            patch.object(oauth.httpx, "AsyncClient", http_cls),
            patch.object(oauth, "_store_in_keychain", store),
            patch.object(oauth, "_provision_oauth_to_n8n", provision),
            patch("laya.integrations.n8n_client.update_credential", update_credential),
            patch(
                "laya.egress.connections._clone_workflows_for_connection",
                AsyncMock(return_value=(1, [])),
            ),
        ):
            result = await handle_callback("the-code", start["state"], REDIRECT)
        rows = await db.execute_fetchall("SELECT * FROM egress_connections")
        return result, rows, store, provision

    @pytest.mark.asyncio
    async def test_updates_existing_row_and_credential(self, db):
        update = AsyncMock(return_value={})
        result, rows, store, provision = await self._reconnect(db, update)

        assert result["status"] == "connected"
        assert result["connection_id"] == "conn_1"
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "Work"
        assert row["status"] == "connected"
        assert row["error_message"] is None
        assert row["last_validated_at"] is not None
        assert row["n8n_credential_id"] == "cred_9"
        assert store.call_args.args[0] == "conn_1"
        assert store.call_args.args[2]["refresh_token"] == "1//r2"
        provision.assert_not_called()
        cred_id, data = update.call_args.args
        assert cred_id == "cred_9"
        assert data["oauthTokenData"]["access_token"] == "ya29.new"
        assert data["clientSecret"] == "shh"

    @pytest.mark.asyncio
    async def test_failed_credential_update_leaves_connection_in_error(self, db):
        from laya.integrations.n8n_client import N8nApiError

        update = AsyncMock(side_effect=N8nApiError(500, "boom"))
        result, rows, _, _ = await self._reconnect(db, update)

        assert result["status"] == "error"
        assert rows[0]["status"] == "error"
        assert "boom" in rows[0]["error_message"]

    @pytest.mark.asyncio
    async def test_missing_credential_is_provisioned(self, db):
        result, rows, _, provision = await self._reconnect(db, AsyncMock(), cred_id=None)

        assert result["status"] == "connected"
        provision.assert_called_once()
        assert rows[0]["n8n_credential_id"] == "cred_new"


class TestOAuthCallbackHtmlEscaping:
    @pytest.mark.asyncio
    async def test_error_message_is_html_escaped(self):
        from laya.api.egress_api import oauth_callback

        payload = "<img src=x onerror=alert(1)>"
        with patch("laya.egress.oauth.handle_callback", AsyncMock(return_value={"error": payload})):
            response = await oauth_callback("code", "state")
            body = response.body.decode("utf-8")

            assert payload not in body
            assert "&lt;img src=x onerror=alert(1)&gt;" in body

    @pytest.mark.asyncio
    async def test_warning_error_message_is_html_escaped(self):
        from laya.api.egress_api import oauth_callback

        payload = '<script>alert("xss")</script>'
        with patch("laya.egress.oauth.handle_callback", AsyncMock(return_value={"error_message": payload})):
            response = await oauth_callback("code", "state")
            body = response.body.decode("utf-8")

            assert payload not in body
            assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in body

