# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""A web page must not be able to drive the engine.

The engine holds no credential of its own: it trusts anything that can reach
127.0.0.1:{port}. A page on any site the user visits can reach that address, and
two separate doors follow from it:

* **Cross-site HTTP.** A *simple* request needs no preflight, and a body-less POST
  is simple — so with the app open, a hostile page can mark every card read
  (`POST /cards/read-all`), archive or delete a card by id, re-run Omni
  resynthesis (which spends the user's LLM budget), resume a paused budget, toggle
  ingestion sources, and more. The response cannot be read (CORS), but the side
  effect is the point.
* **WebSockets are not subject to CORS at all.** A page can open
  `ws://127.0.0.1:{port}/ws` and receive every card-update broadcast, and send the
  same UI messages the app sends — approve/deny and chat included.

One rule covers both doors: `OriginGuardMiddleware` in
`laya/security/origin_guard.py` runs before routing for HTTP and websocket scopes
alike, so no route carries its own copy. A browser cannot forge `Origin`, and
non-browser callers (n8n, curl, the MCP clients) omit it, so "present and unknown"
means cross-site.

What this does NOT do is stop a local process running as the user; that is the
engine token's job (`tests/test_engine_auth.py`, `tests/test_ws_auth.py`).
"""

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

HOSTILE = "https://evil.example"
ALLOWED = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


@pytest.mark.asyncio
class TestCrossSiteHttp:
    async def _post(self, path: str, origin: str | None = None, **kwargs):
        from laya.main import app

        headers = {"origin": origin} if origin else {}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers=headers) as client:
            return await client.post(path, **kwargs)

    async def test_a_cross_site_post_is_refused(self):
        """`POST /prompts/reload` takes no body, so it is a simple request: the
        browser sends it without a preflight and CORS never gets a say."""
        resp = await self._post("/prompts/reload", HOSTILE)
        assert resp.status_code == 403, (
            f"a cross-site page reached {resp.request.url.path} "
            f"(status {resp.status_code})"
        )

    async def test_a_refused_request_does_not_happen(self, db):
        """The rejection has to come *before* the handler, not after it. A 403 that
        arrives once the cards are already marked read is not a guard."""
        from tests.conftest import insert_test_card

        await insert_test_card(db, card_id="card_csrf", event_id="evt_csrf")

        resp = await self._post("/cards/read-all", HOSTILE)
        assert resp.status_code == 403

        rows = await db.execute_fetchall(
            "SELECT read_at FROM action_cards WHERE card_id = 'card_csrf'"
        )
        assert rows[0]["read_at"] is None, "the cross-site request was carried out anyway"

    @pytest.mark.parametrize("origin", ALLOWED)
    async def test_the_apps_own_origins_are_allowed(self, origin):
        """The bundled webview (tauri://localhost, http://tauri.localhost) and the
        dev server must keep working — a guard that breaks the app is not a fix."""
        resp = await self._post("/prompts/reload", origin)
        assert resp.status_code != 403, f"{origin} was refused"

    async def test_a_caller_without_an_origin_is_allowed(self):
        """n8n, curl and the MCP clients are not browsers and send no Origin. They
        are the same-user-process case the token work covers, not this guard."""
        resp = await self._post("/prompts/reload")
        assert resp.status_code == 200, resp.text

    async def test_the_mcp_token_routes_are_covered_by_the_same_guard(self):
        """The token-mutating MCP routes used to carry their own copy of the check.
        The middleware refuses them before the handler, with nothing per-route."""
        from laya.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"origin": HOSTILE}) as client:
            assert (await client.get("/mcp/token/reveal")).status_code == 403
            assert (await client.post("/mcp/token/refresh")).status_code == 403
            assert (await client.delete("/mcp/token")).status_code == 403


class TestCrossSiteWebSocket:
    """WebSockets bypass CORS entirely, so the handshake needs its own check.

    The refusal has to happen *at* the handshake. A test that connects and then
    waits for a message hangs forever against the unfixed code — which is how the
    first version of this file behaved — so these only enter and leave the
    connection: entering it IS the handshake.
    """

    def _connect(self, origin: str | None):
        from laya.main import app

        headers = {"origin": origin} if origin else {}
        return TestClient(app).websocket_connect("/ws", headers=headers)

    def test_a_cross_site_websocket_is_refused(self):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with self._connect(HOSTILE):
                pass  # reaching here at all means the handshake completed

    def test_the_apps_own_websocket_origin_is_allowed(self):
        with self._connect("tauri://localhost") as ws:
            assert ws is not None  # the handshake completed

    def test_a_websocket_route_inherits_the_guard_with_no_code_of_its_own(self):
        """The check lives in the middleware, not in `/ws`: a throwaway app with a
        websocket route that does nothing but accept is still refused cross-site."""
        from fastapi import FastAPI, WebSocket
        from starlette.websockets import WebSocketDisconnect

        from laya.security.origin_guard import OriginGuardMiddleware

        other = FastAPI()
        other.add_middleware(OriginGuardMiddleware)

        @other.websocket("/other")
        async def _other(websocket: WebSocket):
            await websocket.accept()
            await websocket.send_text("hello")

        with pytest.raises(WebSocketDisconnect):
            with TestClient(other).websocket_connect("/other", headers={"origin": HOSTILE}):
                pass
        with TestClient(other).websocket_connect("/other", headers={"origin": "tauri://localhost"}) as ws:
            assert ws.receive_text() == "hello"


class TestThePredicate:
    def test_absent_origin_is_allowed_and_unknown_is_not(self):
        from laya.security.origin_guard import ALLOWED_ORIGINS, origin_is_allowed

        assert origin_is_allowed(None)
        assert origin_is_allowed("")
        for origin in ALLOWED_ORIGINS:
            assert origin_is_allowed(origin)
        for origin in (HOSTILE, "null", "http://localhost:3000", "tauri://evil"):
            assert not origin_is_allowed(origin), origin
