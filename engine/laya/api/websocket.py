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
        self.active_connections.remove(websocket)
        log.info("ws_client_disconnected", total=len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients concurrently."""
        if not self.active_connections:
            return
        data = json.dumps(message)
        results = await asyncio.gather(
            *[c.send_text(data) for c in list(self.active_connections)],
            return_exceptions=True,
        )
        # Remove any connections that errored
        self.active_connections = [
            c for c, r in zip(self.active_connections, results)
            if not isinstance(r, Exception)
        ]


# Singleton instance
manager = ConnectionManager()
