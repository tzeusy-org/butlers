"""Content-free per-session stage timing (bu-dhloi1.180).

A session's total ``duration_ms`` hides where the time went. This module keeps a
small in-process clock per runtime session and records millisecond offsets from the
spawner's origin (the start of ``Spawner._run``) for the stages the daemon can
observe itself:

``row_created_ms``
    session row inserted (everything before it is prompt/context composition).
``invoke_start_ms``
    the runtime adapter is about to be invoked (setup finished).
``first_mcp_request_ms``
    the first HTTP request the spawned CLI made to the butler's MCP endpoint
    (``initialize``): spawn plus CLI startup, as seen from the daemon.
``first_tool_done_ms`` / ``last_tool_done_ms``
    daemon-side completion of the first and last executed MCP tool call.
``invoke_end_ms``
    the adapter returned or raised.
``total_ms``
    taken when the session row is completed.

Plus ``adapter`` (whitelisted integers the adapter itself measured, such as Codex's
``spawn_latency_ms``) and ``tool_surface`` (tool count and schema size of the butler
MCP endpoint, a token estimate of what the model sees).

Stages the process did not observe are absent, never guessed. Nothing recorded here
is message content: only offsets, counts and sizes (RFC 0038 D17). Marks for a
session that was never ``begin``-ed are no-ops, so a caller-supplied session locator
cannot create state. The registry is bounded.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MAX_TRACKED_SESSIONS = 1024

# Heuristic used everywhere else for "tokens from bytes" when no tokenizer is at hand.
_BYTES_PER_TOKEN = 4

_ADAPTER_INT_KEYS = ("spawn_latency_ms", "attempt_count", "mcp_server_count")


@dataclass
class _Clock:
    origin: float
    now: Callable[[], float]
    stages: dict[str, int] = field(default_factory=dict)
    adapter: dict[str, int] = field(default_factory=dict)
    tool_surface: dict[str, int] = field(default_factory=dict)

    def offset_ms(self) -> int:
        return max(0, int((self.now() - self.origin) * 1000))


_lock = threading.Lock()
_clocks: OrderedDict[str, _Clock] = OrderedDict()


def begin(
    session_id: str,
    *,
    origin: float | None = None,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Start the clock for ``session_id``; ``origin`` is a ``time.monotonic`` reading."""
    clock = _Clock(origin=now() if origin is None else origin, now=now)
    with _lock:
        _clocks[session_id] = clock
        _clocks.move_to_end(session_id)
        while len(_clocks) > MAX_TRACKED_SESSIONS:
            _clocks.popitem(last=False)


def mark(session_id: str, stage: str, *, overwrite: bool = False) -> None:
    """Record ``stage``; the first mark wins unless ``overwrite``. No-op if unknown."""
    with _lock:
        clock = _clocks.get(session_id)
        if clock is not None and (overwrite or stage not in clock.stages):
            clock.stages[stage] = clock.offset_ms()


def mark_tool_done(session_id: str) -> None:
    """Record a completed daemon-side tool call: first is kept, last is overwritten."""
    with _lock:
        clock = _clocks.get(session_id)
        if clock is None:
            return
        offset = clock.offset_ms()
        clock.stages.setdefault("first_tool_done_ms", offset)
        clock.stages["last_tool_done_ms"] = offset


def note_adapter(session_id: str, process_info: dict[str, Any] | None) -> None:
    """Copy the whitelisted integer measurements an adapter exposes (never text)."""
    if not isinstance(process_info, dict):
        return
    picked = {
        key: value
        for key in _ADAPTER_INT_KEYS
        if isinstance((value := process_info.get(key)), int) and not isinstance(value, bool)
    }
    if not picked:
        return
    with _lock:
        clock = _clocks.get(session_id)
        if clock is not None:
            clock.adapter.update(picked)


def note_tool_surface(session_id: str, *, tool_count: int | None, schema_bytes: int | None) -> None:
    """Record the size of the model-visible MCP tool surface; absent values stay absent."""
    if tool_count is None or schema_bytes is None:
        return
    with _lock:
        clock = _clocks.get(session_id)
        if clock is not None:
            clock.tool_surface = {
                "tool_count": tool_count,
                "schema_bytes": schema_bytes,
                "schema_tokens_est": schema_bytes // _BYTES_PER_TOKEN,
            }


def _render(clock: _Clock, *, with_total: bool) -> dict[str, Any]:
    out: dict[str, Any] = dict(clock.stages)
    if with_total:
        out["total_ms"] = clock.offset_ms()
    if clock.adapter:
        out["adapter"] = dict(clock.adapter)
    if clock.tool_surface:
        out["tool_surface"] = dict(clock.tool_surface)
    return out


def snapshot(session_id: str) -> dict[str, Any] | None:
    """Current timings without ending the session, or None when unknown."""
    with _lock:
        clock = _clocks.get(session_id)
        return None if clock is None else _render(clock, with_total=False)


def finish(session_id: str) -> dict[str, Any] | None:
    """Pop the clock and return the final timings (with ``total_ms``), or None."""
    with _lock:
        clock = _clocks.pop(session_id, None)
        return None if clock is None else _render(clock, with_total=True)


def discard(session_id: str) -> None:
    """Drop the clock without producing timings."""
    with _lock:
        _clocks.pop(session_id, None)


def tracked_session_count() -> int:
    with _lock:
        return len(_clocks)


def _reset_for_tests() -> None:
    with _lock:
        _clocks.clear()
