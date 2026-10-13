# Engine auth follow-ups: WebSocket and n8n

**Status:** Proposed, two GitHub issues to open
**Date:** 2026-10-07
**Related:** issue #13 (unauthenticated local REST API), PR #40 (REST bearer auth), PR #41 (cross-site Origin guard), `engine/docs/n8n-engine-auth.md`

## Where engine auth stands

After PR #40 every REST router except `/health` requires `Authorization: Bearer <token>`
(`laya/security/engine_auth.py`, `require_engine_auth`). The Tauri shell generates the token per
launch and hands it to the engine as `LAYA_ENGINE_TOKEN` and to the webview as
`window.__LAYA_ENGINE_TOKEN__`.

PR #41 adds an Origin check: a request whose `Origin` header is present and not one of the app's
own five origins is refused. It is applied as an HTTP middleware (all REST routes, including the
auth-exempt ones) and, by a separate hand-written check, at the top of the `/ws` handler. It adds
no credential check anywhere.

Two gaps remain. Both are callers that bearer auth does not cover today:

| Gap | Caller | What is open | Closed by |
|---|---|---|---|
| `/ws` has no credential check | Tauri webview | Any local process, or any non-browser client, can connect, receive every broadcast, and send `approve_action`, `deny_action`, `execute_action`, `chat_message`, `session_control` | Issue 1 |
| n8n routes are exempt from bearer auth | bundled n8n workflows | `POST /events`, `POST /ingestion-errors`, `GET /repos`, `GET /metadata/{key}` accept any local caller (`INTERIM_N8N_EXEMPT_ROUTES` in `engine_auth.py`) | Issue 2 |

The Origin guard narrows both gaps to non-browser callers only. It does not close them.

---

## Issue 1: Authenticate `/ws`, and move the Origin guard to a pure ASGI middleware

**Status: implemented as issue #45 (2026-10-13).** Part A landed as designed
(`OriginGuardMiddleware` in `laya/security/origin_guard.py`; the per-route copies in
`websocket_endpoint` and `mcp_api.py` are gone). Part B changed transport: instead of a
first-frame auth message, the webview offers the app token as a `Sec-WebSocket-Protocol`
entry next to the protocol name `laya.engine.v1`, and `require_ws_engine_auth` (a FastAPI
dependency on the `/ws` route, sharing `token_matches` with the REST bearer check) refuses the
handshake before `accept()`. The reason the doc gave for avoiding the header, that the token's
character set might not satisfy the header-token grammar, does not hold: the shell mints hex and
the keychain fallback mints base64url, both valid, and `ensure_engine_token` warns if an
operator-supplied token is not. Checking before accept means no half-open unauthenticated
socket, no new message type, no UI "authenticating" state and no server-side deadline, and a
refusal is a 403 handshake exactly like the Origin guard's. The text below is the original
proposal.

### Problem

`websocket_endpoint` in `laya/main.py` calls `manager.connect(websocket)`, which accepts the
socket and adds it to the broadcast list with no check at all. Every UI message type in
`laya/api/ws_router.py` is then reachable, including the ones that approve or execute actions and
start chats, which spend LLM budget. CLAUDE.md records this as "`/ws` is not yet authenticated".

A second, smaller problem comes from how PR #41 is built. It uses `@app.middleware("http")`,
which is Starlette's `BaseHTTPMiddleware`. That middleware never sees websocket scopes, so the
Origin check had to be copied by hand into the `/ws` handler. A websocket route added later
inherits nothing, which is exactly the per-endpoint drift the PR set out to remove.
`BaseHTTPMiddleware` also wraps every request in an extra task, which the long-lived MCP SSE
stream and the chat stream pay for a one-line header compare.

### Design

**A. Pure ASGI Origin middleware.** Replace the `@app.middleware("http")` function with an ASGI
class in `laya/security/origin_guard.py`:

```python
class OriginGuardMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            origin = Headers(scope=scope).get("origin")
            if not origin_is_allowed(origin):
                log.warning("cross_site_request_blocked", kind=scope["type"], origin=origin, path=scope["path"])
                if scope["type"] == "http":
                    await JSONResponse({"detail": "cross-site origin not allowed"}, 403)(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
```

Register it with `app.add_middleware(OriginGuardMiddleware)` after the CORS middleware so it is
the outermost layer. Sending `websocket.close` before `websocket.accept` makes the server answer
the handshake with HTTP 403, which is what PR #41's `TestCrossSiteWebSocket` tests already expect.
Then delete the per-route Origin check in `websocket_endpoint` and `_reject_cross_site` in
`laya/api/mcp_api.py`, which the middleware makes unreachable. Reuse `origin_is_allowed` and
`ALLOWED_ORIGINS` from PR #41 as they are.

**B. Token handshake on `/ws`.** Browsers cannot set headers on a WebSocket, so the token cannot
travel as `Authorization`. Putting it in the URL query string leaks it into uvicorn's access log
and any proxy. Use a first-frame handshake instead:

1. The handler accepts the socket but does **not** add it to `manager.active_connections` yet.
2. It waits at most 5 seconds for the first text frame and requires it to be
   `{"type": "auth", "token": "<engine token>"}`.
3. It compares the token with `hmac.compare_digest` against `get_engine_token()` (the same
   comparison `require_engine_auth` uses; extract a shared `token_matches(presented)` helper in
   `engine_auth.py` so the two cannot drift).
4. On success it adds the socket to the manager, replies `{"type": "auth_ok"}`, and enters the
   existing receive loop. On a missing, late, malformed, or wrong frame it closes with code 1008
   and logs `ws_auth_failed`. Nothing is broadcast to a socket before step 4.

Split `ConnectionManager.connect` in `laya/api/websocket.py` into `accept` and `register` so the
handler can accept first and register only after the handshake.

Why not a `Sec-WebSocket-Protocol` subprotocol carrying the token: it works in browsers, but the
token's character set must then satisfy the RFC token grammar and the shell's
`generate_engine_token()` makes no such promise. The first-frame handshake has no such coupling.

**C. UI client.** In `ui/src/lib/stores/websocket.ts`, in `socket.onopen`, send the auth frame
with `getEngineToken()` from `$lib/config` before setting `wsStatus` to `connected`. Set
`connected` only on `auth_ok`. A 1008 close goes through the existing reconnect backoff; cap the
retries when the close reason is an auth failure so a stale token does not spin.

**D. Standalone engine.** With no shell, `get_engine_token()` falls back to the keychain token
(PR #40). The browser-only dev flow already puts the same token in `VITE_LAYA_ENGINE_TOKEN`, so
the UI side needs nothing new.

### Files

- `engine/laya/security/origin_guard.py`: add `OriginGuardMiddleware`.
- `engine/laya/main.py`: register the middleware, remove the `@app.middleware("http")` function
  and the Origin check in `websocket_endpoint`, add the auth handshake.
- `engine/laya/security/engine_auth.py`: extract `token_matches`.
- `engine/laya/api/websocket.py`: split `connect` into `accept` and `register`.
- `engine/laya/api/mcp_api.py`: remove `_reject_cross_site` and its two call sites.
- `ui/src/lib/stores/websocket.ts`: send the auth frame, gate `connected` on `auth_ok`.
- `CLAUDE.md`: replace "`/ws` is not yet authenticated" with the handshake description.

### Tests

- `tests/test_origin_guard.py` (from PR #41) keeps passing unchanged; its websocket tests now
  exercise the middleware instead of the handler.
- New `tests/test_ws_auth.py`: correct token registers and receives a broadcast; wrong token,
  no frame within the deadline, and a non-auth first frame each close with 1008 and never receive
  a broadcast; a second websocket route added to a throwaway app is refused cross-site with no
  per-route code.
- `tests/conftest.py`: the autouse `_bypass_engine_auth_for_existing_tests` fixture overrides a
  FastAPI dependency, which the websocket handler cannot use. Give the test suite a known token
  (patch `get_engine_token`) and have existing websocket tests send the auth frame.
- Live check: `curl -N` on `/mcp/sse` still streams through the new middleware; the Tauri app
  connects and shows `connected` in the status indicator; `websocat ws://127.0.0.1:8420/ws`
  with no frame is closed after 5 seconds.

---

## Issue 2: Give n8n its own engine credential and drop the auth exemptions

**Status: implemented as issue #47.** The token model changed during implementation: instead
of a keychain token pushed into an n8n credential object, the shell mints a per-launch token
and shares it with both processes by environment variable. `engine/docs/n8n-engine-auth.md`
describes what was built; the text below is the original proposal.

### Problem

`laya/security/engine_auth.py` exempts four routes from bearer auth because the bundled n8n
workflows call them with no credential: `POST /events`, `POST /ingestion-errors`, `GET /repos`,
and `GET /metadata/{key}`. Any local process can post synthetic events into the pipeline, which
drives LLM spend and creates Action Cards, or read repo and workflow config. The code marks the
list "interim".

### Design

The full design is written and reviewed in `engine/docs/n8n-engine-auth.md` (2026-09-27). This
issue is its implementation. The short version:

1. **Token.** New `laya/security/n8n_auth.py` with `ensure_n8n_engine_token()`, a long-lived
   `secrets.token_urlsafe(32)` stored in the keychain as `n8n_engine_token`. It is separate from
   the per-launch app token because n8n is long-running and polls continuously.
2. **Credential sync.** The engine pushes the token into one n8n credential of type
   `httpHeaderAuth`, named `Laya Engine`, header `X-Laya-N8n-Token`. The engine is the source of
   truth; recovery from any drift is "engine overwrites n8n's copy" (`sync_engine_credential`).
3. **Placeholder injection.** Workflow templates reference the credential through a Laya-specific
   placeholder `LAYA_ENGINE_CREDENTIAL`, replaced at clone time and on propagation. Every platform
   credential matcher must skip it (Bitbucket Server's own credential is also `httpHeaderAuth`).
   Sixteen nodes across twelve template files change, each with a `meta.laya_version` bump.
4. **Engine check.** New dependency `require_n8n_token` on `POST /events` and
   `POST /ingestion-errors`, with a 10-minute grace window for the previous token after a
   Regenerate. Enforcement is staged: `log` mode until a readiness check confirms every clone
   carries the credential, then `enforce`, persisted in `settings.json` and never downgraded
   automatically. Recovery hatch `LAYA_N8N_AUTH=off`.
5. **Shared routes.** `GET /repos` and `GET /metadata/{key}` are called by both n8n and the UI.
   The design doc deferred them because the webview had no credential at the time. PR #40 has
   since landed, so this issue can finish them: a combined dependency that accepts either the app
   bearer token or the n8n header, and `INTERIM_N8N_EXEMPT_ROUTES` plus `_N8N_METADATA_PREFIX`
   are deleted outright.
6. **Per-clone update tracking** (design doc §8, migration 073, `workflow_update_error` on
   `sources`, "Update workflow" button). The design puts this first because it fixes an existing
   bug where failed clone pushes are recorded as deployed, and the readiness check in step 4
   depends on it.

Decisions already made, recorded in the design doc: keep `workflow_versions.json` per template;
no periodic rotation; no automatic resync on 401 streaks; never replace n8n's built-in trigger
tracking.

### Files

See `engine/docs/n8n-engine-auth.md` §11 for the ordered list. The main ones:

- `engine/laya/security/n8n_auth.py` (new), `engine/laya/security/engine_auth.py`.
- `engine/laya/integrations/n8n_bootstrap.py`, `engine/laya/egress/connections.py`
  (placeholder injection, credential sync, per-clone errors).
- `engine/laya/api/events.py`, `engine/laya/api/ingestion_errors.py`,
  `engine/laya/api/settings_api.py` (`/repos`), `engine/laya/api/metadata_api.py`.
- `n8n/workflows/*.json` (twelve files), `engine/laya/db/migrations/073_*.sql`.
- Settings UI: workflow list warnings, status panel, Resync and Regenerate.

### Tests

Design doc §12 lists them. The ones that matter most for this issue:

- `require_n8n_token`: correct, wrong, and missing token under `log` and `enforce`; previous
  token inside and outside the grace window.
- Readiness goes `log` to `enforce` once and never reverts.
- Placeholder injection touches only the Laya node; platform injection never touches it.
- Existing `tests/test_*events*` and ingestion-error tests send the header or run in `log` mode.
- Integration against real n8n: fresh install, upgrade from the current release, credential
  deleted in the n8n UI then Resync, Regenerate while polling with no events lost.

### Confirm before building

- `PATCH /api/v1/credentials/{id}` works for `httpHeaderAuth` on the bundled n8n version.
- n8n keeps the credential binding when `_update_workflow` PUTs a clone's nodes.

---

## Order

Issue 1 first. It is small, self-contained, and lands the ASGI middleware that Issue 2's routes
also sit behind. Issue 2 is the larger change and already has a full design; its step 6 can start
in parallel because it is independent of the token work.
