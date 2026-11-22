# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Connection Broker — credential management."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from laya.config import MIGRATIONS_DIR
from laya.egress.connections import (
    create_connection,
    list_all_connections,
    remove_connection,
    test_connection as check_connection,
    _validate_credentials,
)
from laya.egress.models import ConnectionResult


class TestValidateCredentials:
    @pytest.mark.asyncio
    async def test_jira_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("jira", {
                "domain": "https://company.atlassian.net",
                "email": "user@co.com",
                "apiToken": "token123",
            })
            assert valid is True
            assert error is None

    @pytest.mark.asyncio
    async def test_jira_invalid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("jira", {
                "domain": "https://company.atlassian.net",
                "email": "user@co.com",
                "apiToken": "bad_token",
            })
            assert valid is False
            assert "Invalid" in error or "credentials" in error.lower()

    @pytest.mark.asyncio
    async def test_jira_missing_fields(self):
        valid, error = await _validate_credentials("jira", {"domain": "x"})
        assert valid is False
        assert "Missing" in error

    @pytest.mark.asyncio
    async def test_github_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("github", {"accessToken": "ghp_abc"})
            assert valid is True

    @pytest.mark.asyncio
    async def test_slack_oauth_skips_validation(self):
        valid, error = await _validate_credentials("slack", {})
        assert valid is True
        assert error is None

    @pytest.mark.asyncio
    async def test_bitbucket_valid(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            valid, error = await _validate_credentials("bitbucket", {
                "email": "user@co.com",
                "accessToken": "pw",
            })
            assert valid is True

    @pytest.mark.asyncio
    async def test_oauth_platform_auto_valid(self):
        valid, error = await _validate_credentials("gmail", {})
        assert valid is True  # OAuth tokens pre-validated during flow

    @pytest.mark.asyncio
    async def test_unknown_platform_passes(self):
        valid, error = await _validate_credentials("notion", {"apiKey": "x"})
        assert valid is True


class TestCreateConnection:
    @pytest.mark.asyncio
    async def test_create_connection_success(self, db):
        """Test successful connection creation with mocked validation and keychain."""
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="n8n_cred_1"):
                    # Mock workflow cloning to prevent creating real workflows in n8n
                    with patch("laya.egress.connections._clone_workflows_for_connection", new_callable=AsyncMock, return_value=(2, [])):
                        result = await create_connection("jira", {"email": "x", "apiToken": "y", "domain": "z"})

                        assert result.status == "connected"
                        assert result.connection_id is not None
                        assert "comment" in result.capabilities

                        # Verify DB record
                        rows = await db.execute_fetchall("SELECT * FROM egress_connections")
                        assert len(rows) == 1
                        assert rows[0]["platform"] == "jira"
                        assert rows[0]["status"] == "connected"

    @pytest.mark.asyncio
    async def test_create_connection_validation_failure(self, db):
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(False, "Bad token")):
            result = await create_connection("jira", {"email": "x"})
            assert result.status == "failed"
            assert "Bad token" in result.error

    @pytest.mark.asyncio
    async def test_trello_connection_provisions_one_board_ingestion(self, db):
        await db.execute("INSERT INTO spaces (space_id, name) VALUES ('work', 'Work')")
        await db.commit()
        credentials = {
            "apiKey": " demo_key ",
            "apiToken": "demo_token",
            "boardId": "https://trello.com/b/AbCd1234/example-board",
        }
        trello_http = AsyncMock()
        trello_http.__aenter__.return_value = trello_http
        trello_http.get.return_value = httpx.Response(200, json=[])
        n8n_http = AsyncMock()
        n8n_http.post.return_value = httpx.Response(201, json={"id": "trello_workflow"})
        provisioned = []

        async def read_provisioned_workflow(workflow_id, *, active):
            rows = await db.execute_fetchall(
                """SELECT m.value, s.platform, s.space_id FROM sources s
                   JOIN metadata m ON m.key = 'trello-config:' || s.workflow_id AND m.space_id = 'default'
                   WHERE s.workflow_id = ?""",
                (workflow_id,),
            )
            provisioned.extend(rows)

        started = datetime.now(timezone.utc).replace(microsecond=0)

        with (
            patch("laya.egress.connections.httpx.AsyncClient", return_value=trello_http),
            patch("laya.security.keychain.get_api_key", return_value="demo_n8n_key"),
            patch("laya.integrations.n8n_client.create_credential", return_value={"id": "trello_cred"}) as provision,
            patch("laya.integrations.n8n_client.activate_workflow", side_effect=read_provisioned_workflow) as activate,
            patch("laya.integrations.n8n_bootstrap._get_existing_workflows", return_value={}),
            patch("laya.integrations.n8n_bootstrap._get_error_handler_id", return_value="error_handler"),
            patch("laya.http_client.get_client", return_value=n8n_http),
        ):
            result = await create_connection("trello", credentials, name="Example board", space_id="work")

        assert result.status == "connected", result.error
        assert result.capabilities == []
        trello_http.get.assert_awaited_once_with(
            "https://api.trello.com/1/boards/AbCd1234/actions",
            params={"limit": 1},
            headers={"Authorization": 'OAuth oauth_consumer_key="demo_key", oauth_token="demo_token"'},
            timeout=10.0,
        )
        provision.assert_awaited_once_with(
            name="Example board",
            n8n_type="trelloApi",
            data={"apiKey": "demo_key", "apiToken": "demo_token"},
            node_type="n8n-nodes-base.trello",
        )
        activate.assert_awaited_once_with("trello_workflow", active=True)
        assert len(provisioned) == 1
        [at_activation] = provisioned
        config = json.loads(at_activation["value"])
        assert at_activation["platform"] == "trello"
        assert at_activation["space_id"] == "work"
        assert config["boardId"] == "AbCd1234"
        assert started <= datetime.fromisoformat(config["pollStartAt"]) <= datetime.now(timezone.utc)
        assert set(config) == {"boardId", "pollStartAt"}
        workflow = n8n_http.post.call_args.kwargs["json"]
        assert workflow["settings"]["errorWorkflow"] == "error_handler"
        bound_nodes = [node for node in workflow["nodes"] if node.get("credentials")]
        assert [node["name"] for node in bound_nodes] == ["Fetch Trello Actions"]
        assert bound_nodes[0]["credentials"]["trelloApi"]["id"] == "trello_cred"

        [source] = await db.execute_fetchall(
            "SELECT platform, workflow_id, source_type FROM sources WHERE connection_id = ?",
            (result.connection_id,),
        )
        assert tuple(source) == ("trello", "trello_workflow", "ingestion")
        [config] = await db.execute_fetchall(
            "SELECT value FROM metadata WHERE key = 'trello-config:trello_workflow'",
        )
        assert config["value"] == at_activation["value"]

    @pytest.mark.asyncio
    async def test_trello_rejects_a_non_board_url(self):
        with patch("laya.egress.connections.httpx.AsyncClient") as client:
            result = await create_connection("trello", {
                "apiKey": "test", "apiToken": "test", "boardId": "https://trello.com/c/AbCd1234",
            })
        assert result.status == "failed"
        assert "board URL" in result.error
        client.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_connection_unknown_platform(self, db):
        result = await create_connection("totally_unknown", {})
        assert result.status == "failed"
        assert "Unknown" in result.error


@pytest.mark.asyncio
async def test_trello_poll_start_migration_uses_original_source_creation(db):
    await db.executemany(
        "INSERT INTO sources (source_id, name, platform, workflow_id, created_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("old", "Existing Trello connection", "trello", "old", "2026-11-22 08:30:00"),
            ("saved", "Trello with a poll start", "trello", "saved", "2026-11-22 08:30:00"),
        ],
    )
    await db.executemany(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        [
            ("trello-config:old", '{"boardId":"AbCd1234"}'),
            ("trello-config:saved", '{"boardId":"XyZa1234","pollStartAt":"2026-11-21T08:30:00Z"}'),
            ("trello-config:unknown", '{"boardId":"NoId1234"}'),
        ],
    )
    await db.commit()

    sql = (MIGRATIONS_DIR / "076_trello_poll_start.sql").read_text(encoding="utf-8")
    await db.executescript(sql)
    rows = await db.execute_fetchall("SELECT key, value FROM metadata")
    configs = {row["key"]: json.loads(row["value"]) for row in rows}
    assert configs == {
        "trello-config:old": {"boardId": "AbCd1234", "pollStartAt": "2026-11-22T08:30:00Z"},
        "trello-config:saved": {"boardId": "XyZa1234", "pollStartAt": "2026-11-21T08:30:00Z"},
        "trello-config:unknown": {"boardId": "NoId1234"},
    }


class TestListConnections:
    @pytest.mark.asyncio
    async def test_list_empty(self, db):
        conns = await list_all_connections()
        assert conns == []

    @pytest.mark.asyncio
    async def test_list_after_create(self, db):
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="cred1"):
                    with patch("laya.egress.connections._clone_workflows_for_connection", new_callable=AsyncMock, return_value=(2, [])):
                        await create_connection("github", {"accessToken": "ghp_abc"}, name="GitHub Main")

        conns = await list_all_connections()
        assert len(conns) == 1
        assert conns[0].platform == "github"
        assert conns[0].name == "GitHub Main"
        assert "comment" in conns[0].capabilities


class TestRemoveConnection:
    @pytest.mark.asyncio
    async def test_remove_connection(self, db):
        # Create first
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="cred1"):
                    with patch("laya.egress.connections._clone_workflows_for_connection", new_callable=AsyncMock, return_value=(2, [])):
                        result = await create_connection("slack", {"accessToken": "xoxb"})

        # Verify exists
        conns = await list_all_connections()
        assert len(conns) == 1

        # Remove
        with patch("laya.egress.connections._remove_from_keychain"):
            with patch("laya.integrations.n8n_client.delete_credential", new_callable=AsyncMock):
                with patch("laya.egress.connections._remove_connection_workflows", new_callable=AsyncMock):
                    await remove_connection(result.connection_id)

        # Verify gone
        conns = await list_all_connections()
        assert len(conns) == 0


class TestCheckConnection:
    @pytest.mark.asyncio
    async def test_revalidate_connection(self, db):
        # Create
        with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
            with patch("laya.egress.connections._store_in_keychain", return_value=True):
                with patch("laya.egress.connections._provision_to_n8n", new_callable=AsyncMock, return_value="cred1"):
                    with patch("laya.egress.connections._clone_workflows_for_connection", new_callable=AsyncMock, return_value=(2, [])):
                        result = await create_connection("github", {"accessToken": "ghp_abc"})

        # Test it
        with patch("laya.egress.connections._get_from_keychain", return_value={"accessToken": "ghp_abc"}):
            with patch("laya.egress.connections._validate_credentials", new_callable=AsyncMock, return_value=(True, None)):
                valid, error = await check_connection(result.connection_id)
                assert valid is True

        # Verify status updated
        conns = await list_all_connections()
        assert conns[0].status == "connected"
        assert conns[0].last_validated_at is not None
