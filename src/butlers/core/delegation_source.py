"""Constructor-owned native delegation writer dispatch without runtime imports.

This private adapter is not a tool, source selector or actor field. Ordinary
unconfigured callers keep their existing Pool/Connection writer contract.
"""

from __future__ import annotations

from typing import Any

_writers: dict[Any, Any] = {}


def register_writer(pool: Any, writer: Any) -> None:
    prior = _writers.get(pool)
    if prior is not None and prior is not writer:
        raise RuntimeError("Native delegation writer is already registered")
    _writers[pool] = writer


def clear_writer(pool: Any, writer: Any) -> None:
    if _writers.get(pool) is writer:
        del _writers[pool]


async def capture_ask(pool: Any, fields: dict, write: Any) -> str | None:
    selected = [writer for writer in _writers.values() if writer.capture_active()]
    if not selected:
        return None
    if len(selected) != 1 or _writers.get(pool) is not selected[0]:
        # An active native producer cannot switch to a caller's Connection or
        # unrelated Pool and quietly fall back to the ordinary writer path.
        raise RuntimeError("Native delegation owning writer differs")
    return await selected[0].capture_ask(fields, write)
