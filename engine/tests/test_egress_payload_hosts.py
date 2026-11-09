# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""The host an executor sends stored credentials to comes from the connection.

The Jira executor builds request URLs from ``jira_base_url`` and the Bitbucket
Server executor from ``base_url`` (plus ``allow_insecure_ssl`` for TLS). A
payload drafted by an LLM, the composer or a chat tool must not be able to set
any of them; ``N8nBackend._build_payload`` removes them and sets them from the
connection's stored credentials.
"""

from unittest.mock import AsyncMock, patch

import pytest

from laya.egress.backends.n8n import N8nBackend
from laya.egress.models import EgressRequest
from laya.egress.tool_handlers import _build_request

ATTACKER = "https://attacker.example"


@pytest.fixture
def no_event_db():
    with patch("laya.egress.enrichment.get_db") as mock_get_db:
        mock_db = AsyncMock()
        mock_db.execute_fetchall = AsyncMock(return_value=[])
        mock_get_db.return_value = mock_db
        yield


def keychain(creds_by_platform: dict):
    return patch(
        "laya.egress.connections._get_from_keychain",
        side_effect=lambda _cid, platform: creds_by_platform.get(platform),
    )


@pytest.mark.asyncio
class TestJira:
    async def test_payload_host_is_replaced_by_the_connection_domain(self, no_event_db):
        request = EgressRequest(
            platform="jira",
            action_type="comment",
            connection_id="conn_jira",
            payload={"issue_key": "PROJ-1", "comment": "hi", "jira_base_url": ATTACKER},
        )
        with keychain({"jira": {"domain": "https://acme.atlassian.net/"}}):
            envelope = await N8nBackend()._build_payload(request)
        assert envelope["payload"]["jira_base_url"] == "https://acme.atlassian.net"

    async def test_missing_connection_domain_fails_before_dispatch(self, no_event_db):
        request = EgressRequest(
            platform="jira",
            action_type="comment",
            connection_id="conn_jira",
            payload={"issue_key": "PROJ-1", "comment": "hi", "jira_base_url": ATTACKER},
        )
        with keychain({"jira": {"email": "a@b.c", "apiToken": "t"}}):
            with pytest.raises(ValueError, match="no site URL"):
                await N8nBackend()._build_payload(request)

    async def test_payload_host_without_a_connection_is_rejected(self, no_event_db):
        request = EgressRequest(
            platform="jira",
            action_type="comment",
            payload={"issue_key": "PROJ-1", "comment": "hi", "jira_base_url": ATTACKER},
        )
        with pytest.raises(ValueError, match="no site URL"):
            await N8nBackend()._build_payload(request)

    async def test_chat_tool_arguments_cannot_set_the_host(self, no_event_db):
        request = _build_request(
            "comment_on_ticket",
            {
                "platform": "jira",
                "ticket_id": "PROJ-1",
                "comment": "hi",
                "jira_base_url": ATTACKER,
            },
            None,
        )
        request.connection_id = "conn_jira"
        with keychain({"jira": {"domain": "https://acme.atlassian.net"}}):
            envelope = await N8nBackend()._build_payload(request)
        assert envelope["payload"]["jira_base_url"] == "https://acme.atlassian.net"
        assert ATTACKER not in str(envelope)


@pytest.mark.asyncio
class TestBitbucketServer:
    @staticmethod
    def _request(**payload_extra) -> EgressRequest:
        return EgressRequest(
            platform="bitbucket_server",
            action_type="comment",
            connection_id="conn_bbs",
            payload={"repo": "PROJ/app", "pr_id": 7, "comment": "hi", **payload_extra},
        )

    async def test_payload_host_and_tls_toggle_come_from_the_connection(self, no_event_db):
        request = self._request(base_url=ATTACKER, allow_insecure_ssl=True)
        with keychain({"bitbucket_server": {"server": "https://bb.corp/", "allowInsecureSsl": False}}):
            envelope = await N8nBackend()._build_payload(request)
        assert envelope["payload"]["base_url"] == "https://bb.corp"
        assert "allow_insecure_ssl" not in envelope["payload"]

    async def test_tls_toggle_is_set_when_the_connection_enables_it(self, no_event_db):
        request = self._request()
        with keychain({"bitbucket_server": {"server": "https://bb.corp", "allowInsecureSsl": True}}):
            envelope = await N8nBackend()._build_payload(request)
        assert envelope["payload"]["allow_insecure_ssl"] is True

    async def test_missing_server_url_fails_before_dispatch(self, no_event_db):
        request = self._request(base_url=ATTACKER)
        with keychain({"bitbucket_server": {}}):
            with pytest.raises(ValueError, match="no server URL"):
                await N8nBackend()._build_payload(request)
