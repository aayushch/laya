# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Who may reach the engine from a browser.

The engine has no credential of its own: it trusts anything that can reach the
loopback port it binds. A page on any site the user visits can reach that address,
so what separates the app from a hostile page is only what the browser reports
about the request.

``Origin`` is that report, and it is trustworthy in both directions: a page cannot
forge it, and callers that are not browsers — n8n, curl, the external MCP clients,
the ASGI test client — do not send it at all. "Present and not in this set"
therefore means cross-site.

Two doors need the same rule, which is why it lives here rather than in either
one of them:

* **HTTP.** A body-less POST is a *simple* request: it is sent without a CORS
  preflight, so the CORS allowlist never gets a say and the handler simply runs.
  ``POST /cards/read-all``, ``POST /omni/resynthesis`` (which spends the user's
  LLM budget) and ``DELETE /cards/{id}`` are all reachable that way from a page.
* **WebSockets.** A handshake is not subject to CORS at all, so without a check
  any page can open ``/ws``, receive every card-update broadcast, and send the same
  UI messages the app sends — approve/deny and chat included.

An absent ``Origin`` is deliberately *allowed*: it cannot be forged either, and
treating it as hostile would break n8n, curl and the MCP clients. That case — a
process running as the same user — is what the engine token covers
(`security/engine_auth.py`, for REST routes and for the ``/ws`` handshake); this
guard is only about browser identity.

``OriginGuardMiddleware`` applies the rule to both doors from one place. It is a
plain ASGI middleware rather than a Starlette ``@app.middleware("http")`` function
because the latter never sees websocket scopes, which would force every websocket
route to repeat the check by hand.
"""

import structlog
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = structlog.get_logger()

# The bundled webview is the app's own client, so it must keep working:
# Windows Tauri v2 serves it from http(s)://tauri.localhost/, macOS and Linux use
# tauri://localhost, and `npm run dev` serves it from the Vite port.
ALLOWED_ORIGINS = frozenset({
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
})


def origin_is_allowed(origin: str | None) -> bool:
    """True when a browser request may proceed. See the module docstring."""
    return not origin or origin in ALLOWED_ORIGINS


class OriginGuardMiddleware:
    """Refuse HTTP requests and WebSocket handshakes from a foreign browser origin.

    Runs before routing, so every route (including the auth-exempt ones) and
    every websocket route inherits the check. For HTTP the answer is a 403 JSON
    body; for a websocket the close frame is sent before ``accept``, which the
    server turns into a refused (403) handshake.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            origin = Headers(scope=scope).get("origin")
            if not origin_is_allowed(origin):
                log.warning(
                    "cross_site_request_blocked",
                    kind=scope["type"],
                    origin=origin,
                    path=scope.get("path"),
                )
                if scope["type"] == "http":
                    response = JSONResponse(
                        {"detail": "cross-site origin not allowed"}, status_code=403
                    )
                    await response(scope, receive, send)
                else:
                    # 1008 is "policy violation".
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
