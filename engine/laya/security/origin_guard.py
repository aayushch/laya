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
process running as the same user — is what the token work in #13 covers, and this
guard does not pretend to close it.
"""

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
