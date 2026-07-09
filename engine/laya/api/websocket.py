# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""WebSocket connection manager and broadcast."""

import asyncio
import json
from typing import Any

import structlog
from fastapi import WebSocket

log = structlog.get_logger()


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info("ws_client_connected", total=len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        # Tolerant: a broadcast prune may have already removed this socket.
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            return
        log.info("ws_client_disconnected", total=len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients concurrently."""
        if not self.active_connections:
            return
        data = json.dumps(message)
        # Snapshot the recipients so results line up with them. Chat streams
        # broadcast per chunk, so a client can connect during the await — zipping
        # results against the *current* (mutated) list misaligned survivors and
        # silently dropped the newcomer from live updates (review §2).
        snapshot = list(self.active_connections)
        results = await asyncio.gather(
            *[c.send_text(data) for c in snapshot],
            return_exceptions=True,
        )
        failed = {c for c, r in zip(snapshot, results) if isinstance(r, Exception)}
        if failed:
            # Filter the CURRENT list so connections added during the await survive.
            self.active_connections = [
                c for c in self.active_connections if c not in failed
            ]


# Singleton instance
manager = ConnectionManager()
