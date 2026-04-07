"""Tracked asyncio task creation.

All fire-and-forget background tasks should use ``create_task`` from this
module instead of ``asyncio.create_task`` directly.  This lets the shutdown
sequence cancel only *our* application tasks — not uvicorn/starlette internals
whose cancellation causes noisy CancelledError tracebacks on exit.
"""

import asyncio
from typing import Any, Coroutine

_tracked: set[asyncio.Task] = set()


def create_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str | None = None,
) -> asyncio.Task:
    """Create an asyncio task and register it for shutdown cancellation."""
    task = asyncio.create_task(coro, name=name)
    _tracked.add(task)
    task.add_done_callback(_tracked.discard)
    return task


async def cancel_all() -> None:
    """Cancel every tracked task and wait for them to finish.

    Called once during engine shutdown.  Only affects tasks created via
    ``create_task`` above — uvicorn, starlette, and other framework tasks
    are left alone so the ASGI shutdown handshake completes cleanly.
    """
    if not _tracked:
        return
    tasks = list(_tracked)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
