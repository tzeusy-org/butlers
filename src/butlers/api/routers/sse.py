"""Server-Sent Events (SSE) endpoint for live dashboard updates.

Streams butler status changes and session lifecycle events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sse"])

# In-memory event bus: subscribers receive events via asyncio.Queue
_subscribers: list[asyncio.Queue] = []

# Sentinel object to signal generator shutdown (used in tests)
_SHUTDOWN = object()


def broadcast(event_type: str, data: dict) -> None:
    """Push an event to all connected SSE subscribers.

    Call this from other parts of the application when butler status
    changes or sessions start/stop.
    """
    payload = {"type": event_type, "data": data, "timestamp": time.time()}
    dead: list[asyncio.Queue] = []
    for q in _subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.remove(q)


async def _event_generator(request: Request) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted events until the client disconnects."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.append(queue)
    try:
        # Send initial connected event
        yield f"event: connected\ndata: {json.dumps({'status': 'ok'})}\n\n"

        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                if event is _SHUTDOWN:
                    break
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
            except TimeoutError:
                # Send keepalive comment every 30s to prevent connection timeout
                yield ": keepalive\n\n"
    finally:
        if queue in _subscribers:
            _subscribers.remove(queue)


@router.get("/events")
async def sse_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for live dashboard updates.

    Event types:
    - connected: Initial connection confirmation
    - butler_status: Butler status change (online/offline/error)
    - session_start: A new runtime session has started
    - session_end: A runtime session has completed
    - heartbeat: Periodic keepalive (comment, not a named event)
    """
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
