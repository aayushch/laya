# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""The `/ws` handshake requires the app token (#45).

The webview offers the token as a `Sec-WebSocket-Protocol` entry next to the
protocol name; `require_ws_engine_auth` checks it before `accept()`. Every
refusal below happens *at* the handshake: entering the `websocket_connect`
context is the handshake, so a refused one raises `WebSocketDisconnect` there.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from laya.main import app
from laya.security import keychain
from laya.security.engine_auth import WS_SUBPROTOCOL, token_matches
from laya.security.keychain import ENGINE_TOKEN_ENV, store_engine_token

HOSTILE = "https://evil.example"
APP_ORIGIN = "tauri://localhost"
TOKEN = "lyae_ws_test_token_1234567890abcdef"


@pytest.fixture(autouse=True)
def setup_engine_token(monkeypatch):
    """A known app token, no pinned process token, and a distinct n8n token."""
    monkeypatch.setattr(keychain, "_process_engine_token", None)
    monkeypatch.delenv(ENGINE_TOKEN_ENV, raising=False)
    store_engine_token(TOKEN)
    monkeypatch.setattr(keychain, "_process_n8n_token", "n8n_token_not_valid_on_ws")
    yield TOKEN


def _connect(subprotocols=None, origin: str | None = APP_ORIGIN, headers: dict | None = None):
    hdrs = dict(headers or {})
    if origin:
        hdrs["origin"] = origin
    return TestClient(app).websocket_connect("/ws", subprotocols=subprotocols, headers=hdrs)


def _is_refused(**kwargs) -> bool:
    try:
        with _connect(**kwargs):
            return False
    except WebSocketDisconnect:
        return True


class TestRefusedHandshakes:
    def test_no_subprotocol_at_all(self):
        assert _is_refused(subprotocols=None)

    def test_protocol_name_without_a_token(self):
        assert _is_refused(subprotocols=[WS_SUBPROTOCOL])

    def test_token_without_the_protocol_name(self):
        assert _is_refused(subprotocols=[TOKEN])

    def test_wrong_token(self):
        assert _is_refused(subprotocols=[WS_SUBPROTOCOL, "not-the-token"])

    def test_two_candidate_tokens_are_ambiguous_and_refused(self):
        assert _is_refused(subprotocols=[WS_SUBPROTOCOL, TOKEN, "extra"])

    def test_the_n8n_token_opens_nothing_on_ws(self):
        """The n8n token is scoped to the four n8n routes (#47); `/ws` is app-token only."""
        assert _is_refused(subprotocols=[WS_SUBPROTOCOL, "n8n_token_not_valid_on_ws"])

    def test_a_valid_token_from_a_hostile_origin_is_still_refused(self):
        """Origin guard and token are independent layers; both must pass."""
        assert _is_refused(subprotocols=[WS_SUBPROTOCOL, TOKEN], origin=HOSTILE)

    def test_a_refused_socket_never_joins_the_broadcast_pool(self):
        from laya.api.websocket import manager

        before = len(manager.active_connections)
        assert _is_refused(subprotocols=[WS_SUBPROTOCOL, "not-the-token"])
        assert len(manager.active_connections) == before


class TestAcceptedHandshakes:
    def test_valid_token_connects_and_receives_broadcasts(self):
        from laya.api.websocket import manager

        with TestClient(app) as client:
            with client.websocket_connect(
                "/ws", subprotocols=[WS_SUBPROTOCOL, TOKEN], headers={"origin": APP_ORIGIN}
            ) as ws:
                assert ws.accepted_subprotocol == WS_SUBPROTOCOL
                client.portal.call(manager.broadcast, {"type": "ping", "payload": {}})
                assert ws.receive_json() == {"type": "ping", "payload": {}}

    def test_no_origin_header_is_fine_when_the_token_is_right(self):
        """A non-browser client holding the token (a test harness, a local tool
        the user runs) is exactly who the token is for."""
        with _connect(subprotocols=[WS_SUBPROTOCOL, TOKEN], origin=None) as ws:
            assert ws.accepted_subprotocol == WS_SUBPROTOCOL

    def test_entries_may_arrive_as_one_comma_joined_header_with_spaces(self):
        with _connect(headers={"sec-websocket-protocol": f"  {WS_SUBPROTOCOL} ,  {TOKEN}  "}) as ws:
            assert ws.accepted_subprotocol == WS_SUBPROTOCOL

    def test_entry_order_does_not_matter(self):
        with _connect(subprotocols=[TOKEN, WS_SUBPROTOCOL]) as ws:
            assert ws.accepted_subprotocol == WS_SUBPROTOCOL


class TestTheCompareHelper:
    def test_matches_only_the_exact_token(self):
        assert token_matches(TOKEN)
        assert not token_matches(TOKEN + "x")
        assert not token_matches("")

    def test_never_matches_when_no_token_is_configured(self, monkeypatch):
        monkeypatch.setattr("laya.security.engine_auth.get_engine_token", lambda: None)
        assert not token_matches(TOKEN)
        assert not token_matches("")

    def test_non_ascii_input_is_a_mismatch_not_a_crash(self):
        assert not token_matches("tökén")
