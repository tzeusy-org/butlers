"""Fleet event bus — multiplexed WebSocket at ``WS /api/events/stream``.

Generalizes the WS→targeted-cache-invalidation pattern first built for the
now-retired dedicated ``/api/approvals/stream`` socket (see
``use-approvals-stream.ts:146-152``), then copied for the now-retired
``/api/spend/stream`` and ``/api/settings/stream`` sockets. Rather than
maintaining a bespoke socket per surface, every surface that wants live
updates subscribes once here and receives a typed envelope::

    {"type": "approval" | "spend" | "session" | "notification" | "issue"
             | "ingestion" | "calendar" | "chronicles" | "header_delta"
             | "attention_add" | "attention_remove" | "heartbeat",
     "ts": <unix float>, "data": {...}}

On connect the server replays a snapshot of recent events (ring buffer) so a
client is never blank while waiting for the next live event. When no event
arrives within ``_HEARTBEAT_INTERVAL_S`` the server sends a synthetic
``heartbeat`` event, giving clients a way to prove liveness even during a
quiet period (the shell's Live indicator uses "have we heard from the socket
recently" to render connected/reconnecting/down).

The three dedicated per-feature WS routes this bus generalized
(``/api/approvals/stream``, ``/api/spend/stream``, ``/api/settings/stream``)
had zero remaining consumers once every dashboard surface migrated onto this
bus, and were deleted in bu-01r64.2. ``emit_approvals_event`` (in
``approvals.py``) still fans approval events onto this bus for API-initiated
lifecycle transitions; daemon-originated events reach this bus via the
Postgres LISTEN/NOTIFY bridge (RFC 0022, ``butlers.fleet_events``) instead of
an upward daemon→api import. A handful of additional choke points (session
lifecycle, notify() delivery, audit-log errors) call ``emit_event`` directly;
``ingest_v1``'s ingestion-events insert reaches this bus through that bridge.
See
``docs/redesigns/2026-07-03-jarvis-audit.md`` move 5.
"""

from __future__ import annotations

import asyncio
import collections
import hmac
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

# ---------------------------------------------------------------------------
# Event types carried on the bus (move 5 §JARVIS audit).
# ---------------------------------------------------------------------------

#: Canonical event types the multiplexed bus promises to carry. Frontend's
#: declarative event->cache-patch registry (event-cache-registry.ts) has one
#: entry per type here; keep the two lists in sync.
EVENT_TYPES = frozenset(
    {
        "session",  # phase: "started" | "ended"
        "notification",  # notify() delivery attempts
        "ingestion",  # new ingestion_events row (emitted from ingest_v1's insert transaction)
        "calendar",  # provider or internal calendar projection completed
        "chronicles",  # chronicler scheduled projection wrote material rows
        "issue",  # a new audit-log error landed (issues feed may have changed)
        "approval",  # approval lifecycle transitions (created/approved/rejected/...)
        "spend",  # per-call cost events
        "header_delta",  # Settings Console header_counts changed (bu-3quv8)
        "attention_add",  # Settings Console attention item appeared (bu-3quv8)
        "attention_remove",  # Settings Console attention item cleared (bu-3quv8)
        "heartbeat",  # synthetic keepalive; no `data`
    }
)

# Ring buffer of the last N events across all types (snapshot-on-connect).
_RING_BUFFER_SIZE = 200
_events_ring: collections.deque[dict] = collections.deque(maxlen=_RING_BUFFER_SIZE)

# Per-subscriber asyncio.Queue; filled by emit_event(), drained by the WS handler.
_events_subscribers: list[asyncio.Queue] = []

_EVENTS_QUEUE_MAXSIZE = 512
_EVENTS_HEARTBEAT_INTERVAL_S = 20.0


def emit_event(event_type: str, data: dict[str, Any] | None = None, **extra: Any) -> None:
    """Publish one event onto the fleet event bus.

    Adds the event to the ring buffer (snapshot-on-connect) and broadcasts it
    to every connected subscriber queue. Drops slow subscribers whose queues
    are full rather than blocking the emitting call site — this function is
    called from hot paths (session close, notify() delivery, audit-log
    writes) and must never be able to stall them.

    ``event_type`` should be one of :data:`EVENT_TYPES`, but unknown types are
    still accepted (forwards-compatible with new producers) — only logged at
    debug level.
    """
    if event_type not in EVENT_TYPES:
        logger.debug("emit_event: unrecognised event_type %r (forwarding anyway)", event_type)

    event: dict[str, Any] = {
        "type": event_type,
        "ts": time.time(),
        "data": data or {},
    }
    if extra:
        event.update(extra)

    _events_ring.append(event)

    dead: list[asyncio.Queue] = []
    for q in _events_subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _events_subscribers.remove(q)
        except ValueError:
            pass


def _reset_events_bus_for_tests() -> None:
    """Clear ring buffer and subscribers. Test-only helper."""
    _events_ring.clear()
    _events_subscribers.clear()


def _auth_ws_api_key(token: str | None) -> bool:
    """Return True if *token* matches the configured DASHBOARD_API_KEY.

    Mirrors the auth helpers in approvals.py / spend.py / settings_console.py:
    when DASHBOARD_API_KEY is not set, all tokens are accepted (dev mode).
    """
    expected = os.environ.get("DASHBOARD_API_KEY") or None
    if expected is None:
        return True
    if not token:
        return False
    return hmac.compare_digest(token, expected)


@router.websocket("/stream")
async def events_stream(
    websocket: WebSocket,
    api_key: str | None = Query(None),
) -> None:
    """WebSocket stream multiplexing every dashboard-relevant event (move 5).

    Authentication: pass the dashboard API key via ``?api_key=<key>`` at
    upgrade time (browsers cannot set ``X-API-Key`` headers on WS upgrades).
    Closes with WS code 4401 on auth failure.

    On connect the server sends a ``snapshot`` message containing the recent
    ring buffer (up to the last 200 events across all types) so a client is
    never blank while waiting for the next live event. Subsequent messages
    are individual typed events as they occur, or a synthetic ``heartbeat``
    event when the connection has been idle for
    ``_EVENTS_HEARTBEAT_INTERVAL_S`` seconds.
    """
    if not _auth_ws_api_key(api_key):
        await websocket.close(code=4401)
        return

    await websocket.accept()

    # Subscribe BEFORE sending the snapshot: if we snapshotted first, an event
    # emitted in the gap between snapshot-send and subscribe would never reach
    # this client (missed entirely, not just duplicated). Subscribing first
    # means the worst case is a harmless duplicate — an event that lands in
    # the gap appears in both the snapshot (via the ring buffer) and the live
    # queue — which is fine because replay is idempotent (cache patches are
    # invalidateQueries-only, see bu-86c4c.8 review).
    queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENTS_QUEUE_MAXSIZE)
    _events_subscribers.append(queue)
    try:
        snapshot = {"type": "snapshot", "ts": time.time(), "events": list(_events_ring)}
        try:
            await websocket.send_text(json.dumps(snapshot))
        except WebSocketDisconnect:
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_EVENTS_HEARTBEAT_INTERVAL_S)
                await websocket.send_text(json.dumps(event))
            except TimeoutError:
                try:
                    await websocket.send_text(
                        json.dumps({"type": "heartbeat", "ts": time.time(), "data": {}})
                    )
                except WebSocketDisconnect:
                    break
            except WebSocketDisconnect:
                break
    finally:
        try:
            _events_subscribers.remove(queue)
        except ValueError:
            pass
