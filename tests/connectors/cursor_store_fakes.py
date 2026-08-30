"""Signature-bound test doubles for cursor-store calls."""

from __future__ import annotations

from inspect import signature
from typing import Any

from butlers.connectors.cursor_store import save_cursor


class RecordingSaveCursor:
    """Record valid cursor saves and name drift at the shared fake boundary."""

    __signature__ = signature(save_cursor)

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        try:
            bound = signature(save_cursor).bind(*args, **kwargs)
        except TypeError as exc:
            raise TypeError(f"save_cursor fake signature mismatch: {exc}") from None

        bound.apply_defaults()
        self.calls.append(dict(bound.arguments))
