"""Shared httpx.AsyncClient for connection reuse across the engine.

Instead of creating a new ``httpx.AsyncClient`` per request (which opens a
fresh TCP connection every time), all engine code should use ``get_client()``
to obtain a long-lived client whose internal connection pool keeps idle
connections open for reuse.
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Return the shared async HTTP client, creating it lazily if needed."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60,
            ),
        )
    return _client


async def close_client() -> None:
    """Close the shared client (call during app shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
