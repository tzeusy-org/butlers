"""A pool double for ``FilteredEventBuffer`` writes (park and flush)."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any


class FilteredEventsPool:
    """Captures ``connectors.filtered_events`` INSERTs; ``fail_writes`` makes them raise.

    Other reads answer empty, so a replay drain against it finds nothing.
    """

    def __init__(self) -> None:
        self.fail_writes = False
        self.written: list[tuple[Any, ...]] = []

    async def execute(self, *args: Any) -> None:
        return None

    async def fetch(self, *args: Any) -> list[Any]:
        return []

    async def fetchrow(self, *args: Any) -> None:
        return None

    async def fetchval(self, *args: Any) -> None:
        return None

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncIterator[FilteredEventsPool]:
        yield self

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield None

    async def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        if self.fail_writes:
            raise OSError("connectors database unreachable")
        if "filtered_events" in sql:
            self.written.extend(rows)
