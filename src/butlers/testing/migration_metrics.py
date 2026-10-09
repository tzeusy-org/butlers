"""Bounded operation clocks for the existing per-test JUnit evidence carrier.

These observations do not qualify a performance claim. They contain no database,
role, key, path, SQL or exception operands. Nested spans are explicitly retained
as spans, never advertised as an additive wall-time total.
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps

_PHASES = frozenset(
    {
        "key-hash",
        "fixture-total",
        "cold-build",
        "clone-database",
        "database-acl",
        "database-settings",
        "parity-readback",
        "pool-connect",
        "pool-close",
        "cleanup-database",
        "cleanup-role",
    }
)
_MODES = frozenset({"fresh", "cloned", "fresh-reference"})
_MAX_EVENTS = 4096


@dataclass
class _Capture:
    policy: str
    events: list[dict] = field(default_factory=list)
    complete: bool = True
    active: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, phase: str, mode: str | None, elapsed: float, success: bool) -> None:
        with self.lock:
            if not self.active:
                return
            if not math.isfinite(elapsed) or elapsed < 0 or len(self.events) >= _MAX_EVENTS:
                self.complete = False
                return
            self.events.append(
                {"phase": phase, "mode": mode, "elapsed_s": elapsed, "success": success}
            )

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "schema": 1,
                "policy": self.policy,
                "complete": self.complete,
                "spans": [dict(event) for event in self.events],
            }


_CURRENT: ContextVar[_Capture | None] = ContextVar("migration_provisioning_capture", default=None)


def begin(policy: str):
    if policy not in {"fresh", "cloned-eligible"}:
        raise ValueError("unknown migration fixture policy")
    return _CURRENT.set(_Capture(policy))


def snapshot() -> dict | None:
    owner = _CURRENT.get()
    return owner.snapshot() if owner is not None else None


def finish(token) -> None:
    owner = _CURRENT.get()
    if owner is not None:
        with owner.lock:
            owner.active = False
    _CURRENT.reset(token)


@contextmanager
def measure(phase: str, *, mode: str | None = None):
    if phase not in _PHASES or (mode is not None and mode not in _MODES):
        raise ValueError("unknown migration provisioning span")
    owner = _CURRENT.get()
    if owner is None:
        yield
        return
    started = time.perf_counter()
    success = False
    try:
        yield
        success = True
    finally:
        owner.record(phase, mode, time.perf_counter() - started, success)


def measured(phase: str, *, mode: str | None = None):
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            with measure(phase, mode=mode):
                return function(*args, **kwargs)

        return observed

    return decorate
