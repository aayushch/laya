# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Engine API authentication dependencies.

Every REST route takes the app bearer token (`Authorization: Bearer`). The
few routes the bundled n8n workflows call also take the n8n token in the
`X-Laya-N8n-Token` header, and nothing else does: a caller holding only the
n8n token cannot reach any other route. On those routes a request with
neither credential is handled according to the n8n enforcement mode
(`security/n8n_auth.py`): logged and let through in `log` mode, rejected in
`enforce` mode.

The `/ws` handshake takes the same app token and nothing else (the n8n token
and the MCP token open nothing here). A browser cannot set `Authorization` on
a WebSocket, and a query string would land in access logs, so the webview
offers the token as a `Sec-WebSocket-Protocol` entry next to the protocol
name `WS_SUBPROTOCOL`. `require_ws_engine_auth` checks it before the socket
is accepted, so a refused handshake answers 403 exactly like the Origin guard,
and a socket that reaches `accept()` is always authenticated.
"""

from __future__ import annotations

import hmac

import structlog
from fastapi import HTTPException, Request, WebSocket, WebSocketException, status

from laya.security import n8n_auth
from laya.security.keychain import get_engine_token

log = structlog.get_logger()

# Routes the bundled n8n workflows call. Exact (method, path) matches; the
# UI also calls GET /repos, so each route accepts either credential.
N8N_ROUTES: set[tuple[str, str]] = {
    ("POST", "/events"),
    ("GET", "/repos"),
    ("POST", "/ingestion-errors"),
}

# n8n reads single metadata keys (GET /metadata/{key}). Listing (`/metadata`)
# and writing metadata are app-token only: a metadata write can repoint a
# workflow's config (e.g. the Bitbucket Server URL its credential is sent to).
_N8N_METADATA_PREFIX = "/metadata/"

# Vendor OAuth redirects arrive from the user's browser with no bearer header.
VENDOR_OAUTH_EXEMPT_ROUTES: set[tuple[str, str]] = {
    ("GET", "/egress/connections/oauth/callback"),
}


def is_n8n_route(method: str, path: str) -> bool:
    """True for the routes that accept the n8n token as well as the app token."""
    if (method, path) in N8N_ROUTES:
        return True
    return method == "GET" and path.startswith(_N8N_METADATA_PREFIX) and len(path) > len(_N8N_METADATA_PREFIX)


def is_auth_exempt(method: str, path: str) -> bool:
    """True for routes that take no credential at all (the vendor OAuth callback)."""
    return (method, path) in VENDOR_OAUTH_EXEMPT_ROUTES


def _unauthorized(detail: str = "Unauthorized") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def token_matches(presented: str) -> bool:
    """True when `presented` is the app token. Shared by REST and `/ws`."""
    expected = get_engine_token()
    # Compare as bytes: hmac.compare_digest raises TypeError on non-ASCII str,
    # and header values are latin-1 decoded, so a credential containing bytes
    # >= 0x80 would surface as a 500 instead of a 401.
    return bool(presented) and bool(expected) and hmac.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    )


def bearer_matches(request: Request) -> bool:
    """True when the request carries the app bearer token."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return False
    return token_matches(auth_header[7:].strip())


# ---------------------------------------------------------------------------
# WebSocket handshake
# ---------------------------------------------------------------------------

# Subprotocol name the webview offers together with the token, and the one the
# engine echoes back on accept (a browser aborts the connection unless the
# server selects one of the offered names).
WS_SUBPROTOCOL = "laya.engine.v1"


def offered_subprotocols(websocket: WebSocket) -> list[str]:
    """The `Sec-WebSocket-Protocol` entries a client offered, in order.

    A client may send them as one comma-separated header value or as several
    header lines; both forms are flattened here.
    """
    entries: list[str] = []
    for raw in websocket.headers.getlist("sec-websocket-protocol"):
        entries.extend(part.strip() for part in raw.split(",") if part.strip())
    return entries


async def require_ws_engine_auth(websocket: WebSocket) -> None:
    """Authenticate a `/ws` handshake; refuse it before `accept()` otherwise.

    The client must offer `WS_SUBPROTOCOL` plus exactly one other entry, and
    that entry must be the app token. Raising `WebSocketException` here makes
    Starlette close the socket unaccepted, which the server reports to the
    client as a 403 handshake.
    """
    entries = offered_subprotocols(websocket)
    candidates = [e for e in entries if e != WS_SUBPROTOCOL]
    if WS_SUBPROTOCOL not in entries or len(candidates) != 1:
        log.warning("ws_auth_failed", reason="no_token", offered=len(entries))
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
    if not token_matches(candidates[0]):
        log.warning("ws_auth_failed", reason="bad_token")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")


async def require_engine_auth(request: Request) -> str:
    """Authenticate an incoming REST request.

    Returns the app token, "n8n" when the n8n token was used, or "" when the
    route is exempt or let through in n8n log mode. GET /health is exempted at
    the router inclusion level in main.py.
    """
    path = request.url.path.rstrip("/")
    method = request.method
    if is_auth_exempt(method, path):
        return ""

    if bearer_matches(request):
        return request.headers["authorization"][7:].strip()

    if is_n8n_route(method, path):
        if n8n_auth.header_matches(request):
            return "n8n"
        enforced = n8n_auth.get_mode() == n8n_auth.MODE_ENFORCE
        n8n_auth.record_rejection(request, enforced=enforced)
        if not enforced:
            return ""
        raise _unauthorized(
            "n8n token missing or invalid. Check the n8n engine credential status "
            "under Settings → Integrations → n8n."
        )

    log.warning("engine_auth_failed", path=path, method=method)
    raise _unauthorized()
