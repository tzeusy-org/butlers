"""Home Assistant connector — REST polling fallback (tasks 4.1–4.4).

This module implements the REST polling fallback for the HA connector process.
It is activated when the WebSocket connection has failed 3 consecutive times
and deactivated when the WebSocket successfully reconnects.

Components:
- ``HAStateCache``: In-memory state cache mapping entity_id → state snapshot.
  Supports diff-based change detection between consecutive polls.
- ``HARestPoller``: Asyncio-based polling loop that calls ``GET /api/states``
  at a configurable interval (``HA_POLL_INTERVAL_S``, default 60s) and emits
  detected state-changed diffs via a callback.
- ``HAFallbackController``: Tracks consecutive WS failure count and triggers
  REST fallback activation after the threshold (3 failures) is reached; deactivates
  on WS reconnect.

Usage in the HA connector::

    from butlers.connectors.home_assistant_rest import (
        HAFallbackController,
        HARestPoller,
        HAStateCache,
    )

    # Create cache and poller
    cache = HAStateCache()
    poller = HARestPoller(
        base_url="http://homeassistant.local:8123",
        access_token="my-token",
        state_cache=cache,
        poll_interval_s=60,
        on_state_changed=my_event_callback,
    )

    # Fallback controller — wired to connector state mutators
    controller = HAFallbackController(
        ws_failure_threshold=3,
        on_fallback_start=poller.start,
        on_fallback_stop=poller.stop,
    )

    # When WS disconnects:
    controller.on_ws_failure()  # call once per failed attempt

    # When WS reconnects:
    controller.on_ws_success()  # resets counter and stops REST polling
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_POLL_INTERVAL_S = 60
_DEFAULT_WS_FAILURE_THRESHOLD = 3

# ---------------------------------------------------------------------------
# State snapshot (task 4.2)
# ---------------------------------------------------------------------------


@dataclass
class EntityStateSnapshot:
    """Snapshot of a single HA entity's state as returned by ``GET /api/states``.

    Stores the minimal fields needed for diff-based change detection and
    ``state_changed`` event generation.
    """

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str = ""
    last_updated: str = ""

    @classmethod
    def from_ha_state(cls, ha_state: dict[str, Any]) -> EntityStateSnapshot:
        """Construct from a raw HA state dict (as returned by ``GET /api/states``).

        Args:
            ha_state: A single state object from the HA REST API response.

        Returns:
            ``EntityStateSnapshot`` populated from the HA state dict.
        """
        return cls(
            entity_id=ha_state.get("entity_id", ""),
            state=ha_state.get("state", ""),
            attributes=ha_state.get("attributes", {}),
            last_changed=ha_state.get("last_changed", ""),
            last_updated=ha_state.get("last_updated", ""),
        )

    def has_state_changed(self, other: EntityStateSnapshot) -> bool:
        """Return True if the entity state value changed between this and ``other``.

        Compares only the ``state`` field (the primary state string, e.g. ``"on"``,
        ``"22.5"``, ``"locked"``).  Attribute-only changes are intentionally ignored
        to reduce noise.

        Args:
            other: A newer snapshot of the same entity.

        Returns:
            ``True`` if ``self.state != other.state``; ``False`` otherwise.
        """
        return self.state != other.state


# ---------------------------------------------------------------------------
# In-memory state cache (task 4.2)
# ---------------------------------------------------------------------------


class HAStateCache:
    """In-memory cache mapping entity_id → ``EntityStateSnapshot``.

    Supports diff-based change detection between consecutive REST poll cycles.
    Thread-safe only for asyncio single-threaded use (no locks used).
    """

    def __init__(self) -> None:
        self._cache: dict[str, EntityStateSnapshot] = {}

    def __len__(self) -> int:
        """Return the number of tracked entities."""
        return len(self._cache)

    def __contains__(self, entity_id: object) -> bool:
        """Return True if ``entity_id`` is in the cache."""
        return entity_id in self._cache

    def get(self, entity_id: str) -> EntityStateSnapshot | None:
        """Return the cached snapshot for ``entity_id``, or ``None`` if not cached.

        Args:
            entity_id: HA entity ID to look up.

        Returns:
            Cached snapshot, or ``None``.
        """
        return self._cache.get(entity_id)

    def update(self, snapshot: EntityStateSnapshot) -> None:
        """Store or replace the snapshot for ``snapshot.entity_id``.

        Args:
            snapshot: New entity state snapshot to persist.
        """
        self._cache[snapshot.entity_id] = snapshot

    def diff(
        self,
        new_states: list[EntityStateSnapshot],
    ) -> list[tuple[EntityStateSnapshot | None, EntityStateSnapshot]]:
        """Compute state-changed diffs between the cache and a fresh state list.

        For each snapshot in ``new_states``:
        - If the entity is not in the cache → it is a new entity; yielded as
          ``(None, new_snapshot)`` (treat as a state change from nothing).
        - If the entity state value changed → yielded as ``(old_snapshot, new_snapshot)``.
        - If the state is unchanged → not included.

        The cache is NOT updated by this call; callers must call :meth:`apply`
        to commit the new states.

        Args:
            new_states: Fresh list of entity state snapshots from a poll cycle.

        Returns:
            List of ``(old_or_None, new_snapshot)`` tuples for changed entities.
        """
        changes: list[tuple[EntityStateSnapshot | None, EntityStateSnapshot]] = []
        for new_snap in new_states:
            old_snap = self._cache.get(new_snap.entity_id)
            if old_snap is None:
                # New entity — emit as a state change from "unknown"
                changes.append((None, new_snap))
            elif old_snap.has_state_changed(new_snap):
                changes.append((old_snap, new_snap))
        return changes

    def apply(self, new_states: list[EntityStateSnapshot]) -> None:
        """Replace the cache contents with ``new_states``.

        Entities present in the old cache but absent from ``new_states`` are
        removed (they no longer exist in HA or have been filtered out).

        Args:
            new_states: Complete set of entity snapshots from a poll cycle.
        """
        self._cache = {snap.entity_id: snap for snap in new_states}

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._cache.clear()


# ---------------------------------------------------------------------------
# State-changed event dict builder (used by REST poller to match WS event shape)
# ---------------------------------------------------------------------------


def build_rest_state_changed_event(
    old_snap: EntityStateSnapshot | None,
    new_snap: EntityStateSnapshot,
    time_fired: str,
) -> dict[str, Any]:
    """Build a synthetic HA ``state_changed`` event dict from REST poll diffs.

    The shape mimics a HA WebSocket ``state_changed`` event so that the rest
    of the connector pipeline (filters, envelope builder) can process REST diffs
    and WS events via the same code path.

    Args:
        old_snap: Previous cached snapshot for this entity, or ``None`` if new.
        new_snap: Current snapshot from the REST poll.
        time_fired: ISO 8601 timestamp to attach; typically the poll timestamp.

    Returns:
        Dict shaped as a HA ``state_changed`` event.
    """
    old_state_dict: dict[str, Any] | None = None
    if old_snap is not None:
        old_state_dict = {
            "entity_id": old_snap.entity_id,
            "state": old_snap.state,
            "attributes": old_snap.attributes,
            "last_changed": old_snap.last_changed,
            "last_updated": old_snap.last_updated,
        }

    new_state_dict: dict[str, Any] = {
        "entity_id": new_snap.entity_id,
        "state": new_snap.state,
        "attributes": new_snap.attributes,
        "last_changed": new_snap.last_changed or time_fired,
        "last_updated": new_snap.last_updated or time_fired,
    }

    return {
        "event_type": "state_changed",
        "time_fired": time_fired,
        "origin": "LOCAL",
        "data": {
            "entity_id": new_snap.entity_id,
            "old_state": old_state_dict,
            "new_state": new_state_dict,
        },
    }


# ---------------------------------------------------------------------------
# REST polling client (tasks 4.1 and 4.4)
# ---------------------------------------------------------------------------


class HARestPoller:
    """Async REST polling client for ``GET /api/states``.

    Polls HA's REST API at a configurable interval (``poll_interval_s``) and
    emits detected state-changed diffs via the ``on_state_changed`` callback.

    The poll loop runs as an asyncio task while active.  Use :meth:`start` /
    :meth:`stop` to manage the lifecycle.

    Args:
        base_url: HA instance base URL, e.g. ``"http://homeassistant.local:8123"``.
        access_token: HA long-lived access token (bearer token).
        state_cache: Shared ``HAStateCache`` for diff-based change detection.
        poll_interval_s: Seconds between poll cycles (default 60).
        on_state_changed: Async callback invoked for each state-changed diff.
            Signature: ``async (old_snap, new_snap, event_dict) -> None``.
        on_poll_success: Optional callback invoked after each successful poll.
            Called with no arguments.
        on_poll_error: Optional callback invoked after each failed poll.
            Called with the exception.
    """

    def __init__(
        self,
        base_url: str,
        access_token: str,
        state_cache: HAStateCache,
        poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S,
        on_state_changed: Callable[
            [EntityStateSnapshot | None, EntityStateSnapshot, dict[str, Any]],
            Awaitable[None],
        ]
        | None = None,
        on_poll_success: Callable[[], None] | None = None,
        on_poll_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._state_cache = state_cache
        self._poll_interval_s = poll_interval_s
        self._on_state_changed = on_state_changed
        self._on_poll_success = on_poll_success
        self._on_poll_error = on_poll_error

        self._task: asyncio.Task[None] | None = None
        self._shutdown: bool = False

    @property
    def is_running(self) -> bool:
        """Return True if the poll loop task is active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the REST polling loop as a background asyncio task.

        Idempotent — if the loop is already running, this is a no-op.
        """
        if self.is_running:
            logger.debug("HARestPoller: already running, ignoring start()")
            return
        self._shutdown = False
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "HARestPoller: started (interval=%ds, base_url=%s)",
            self._poll_interval_s,
            self._base_url,
        )

    def stop(self) -> None:
        """Stop the REST polling loop.

        Cancels the background task.  Idempotent — safe to call when not running.
        """
        self._shutdown = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("HARestPoller: stopped")
        self._task = None

    async def poll_once(self) -> list[tuple[EntityStateSnapshot | None, EntityStateSnapshot]]:
        """Perform a single ``GET /api/states`` poll and return state-changed diffs.

        Fetches all entity states, computes diffs against the current cache, then
        applies the new states to the cache.  Invokes ``on_state_changed`` for each
        detected change.

        Returns:
            List of ``(old_or_None, new_snapshot)`` tuples for changed entities.

        Raises:
            Exception: Any network or HTTP error is propagated to the caller.
        """
        url = f"{self._base_url}/api/states"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                resp.raise_for_status()
                raw_states: list[dict[str, Any]] = await resp.json()

        snapshots = [EntityStateSnapshot.from_ha_state(s) for s in raw_states if s.get("entity_id")]

        diffs = self._state_cache.diff(snapshots)
        self._state_cache.apply(snapshots)

        if self._on_poll_success is not None:
            self._on_poll_success()

        if diffs and self._on_state_changed is not None:
            time_fired = datetime.now(UTC).isoformat()
            for old_snap, new_snap in diffs:
                event_dict = build_rest_state_changed_event(old_snap, new_snap, time_fired)
                try:
                    await self._on_state_changed(old_snap, new_snap, event_dict)
                except Exception:
                    logger.warning(
                        "HARestPoller: on_state_changed callback raised for entity=%s",
                        new_snap.entity_id,
                        exc_info=True,
                    )

        return diffs

    async def _poll_loop(self) -> None:
        """Main poll loop — runs until cancelled or ``stop()`` is called.

        Polls immediately on start, then sleeps ``poll_interval_s`` between
        subsequent cycles.  Failures are logged and invoke ``on_poll_error``;
        the loop continues.
        """
        try:
            while not self._shutdown:
                try:
                    diffs = await self.poll_once()
                    logger.debug(
                        "HARestPoller: poll complete — %d entities tracked, %d changes",
                        len(self._state_cache),
                        len(diffs),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("HARestPoller: poll failed: %s", exc)
                    if self._on_poll_error is not None:
                        self._on_poll_error(exc)

                if self._shutdown:
                    break
                await asyncio.sleep(self._poll_interval_s)

        except asyncio.CancelledError:
            logger.debug("HARestPoller: poll loop cancelled")
            return
        except Exception as exc:
            logger.error("HARestPoller: unexpected error in poll loop: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Fallback controller — activation after N WS failures (task 4.3)
# ---------------------------------------------------------------------------


class HAFallbackController:
    """Track consecutive WebSocket failure count and control REST fallback lifecycle.

    Activates REST polling fallback after ``ws_failure_threshold`` consecutive WS
    failures and deactivates it on WS reconnect success.

    Args:
        ws_failure_threshold: Number of consecutive WS failures before fallback
            is activated (default 3, per spec §4 task 4.3).
        on_fallback_start: Callable invoked (synchronously) when REST fallback
            should start.  Typically :meth:`HARestPoller.start`.
        on_fallback_stop: Callable invoked (synchronously) when REST fallback
            should stop.  Typically :meth:`HARestPoller.stop`.
    """

    def __init__(
        self,
        ws_failure_threshold: int = _DEFAULT_WS_FAILURE_THRESHOLD,
        on_fallback_start: Callable[[], None] | None = None,
        on_fallback_stop: Callable[[], None] | None = None,
    ) -> None:
        self._threshold = ws_failure_threshold
        self._on_fallback_start = on_fallback_start
        self._on_fallback_stop = on_fallback_stop
        self._consecutive_failures: int = 0
        self._fallback_active: bool = False
        self._last_failure_time: float | None = None

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive WS failure count."""
        return self._consecutive_failures

    @property
    def fallback_active(self) -> bool:
        """True if REST polling fallback is currently active."""
        return self._fallback_active

    def on_ws_failure(self) -> bool:
        """Record a WS connection failure.

        Increments the consecutive failure counter.  If the threshold is reached
        and fallback is not already active, invokes ``on_fallback_start``.

        Returns:
            ``True`` if this failure triggered fallback activation; ``False`` otherwise.
        """
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()
        logger.info(
            "HAFallbackController: WS failure #%d (threshold=%d)",
            self._consecutive_failures,
            self._threshold,
        )

        if not self._fallback_active and self._consecutive_failures >= self._threshold:
            self._fallback_active = True
            logger.warning(
                "HAFallbackController: %d consecutive WS failures — activating REST polling fallback",  # noqa: E501
                self._consecutive_failures,
            )
            if self._on_fallback_start is not None:
                self._on_fallback_start()
            return True

        return False

    def on_ws_success(self) -> bool:
        """Record a successful WS reconnection.

        Resets the consecutive failure counter.  If REST fallback was active,
        invokes ``on_fallback_stop``.

        Returns:
            ``True`` if this success stopped an active fallback; ``False`` otherwise.
        """
        self._consecutive_failures = 0
        self._last_failure_time = None

        if self._fallback_active:
            self._fallback_active = False
            logger.info("HAFallbackController: WS reconnected — deactivating REST polling fallback")
            if self._on_fallback_stop is not None:
                self._on_fallback_stop()
            return True

        return False

    def reset(self) -> None:
        """Reset the controller to initial state without invoking callbacks.

        Intended for use during connector shutdown or test teardown.
        """
        self._consecutive_failures = 0
        self._fallback_active = False
        self._last_failure_time = None
