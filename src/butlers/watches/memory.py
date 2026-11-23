"""In-memory ``WatchStore`` with the cap and version CAS the SQL store will enforce (dark)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from butlers.watches.model import CapExceeded, Watch, WatchConflict, WatchState


class InMemoryWatchStore:
    def __init__(self) -> None:
        self.watches: dict[str, Watch] = {}
        self._lock = asyncio.Lock()
        self._transition_locks: dict[str, list] = {}  # id -> [lock, holders and waiters]

    @asynccontextmanager
    async def serialized(self, watch_id: str) -> AsyncIterator[None]:
        entry = self._transition_locks.setdefault(watch_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._transition_locks.pop(watch_id, None)  # evict idle locks

    async def get(self, watch_id: str) -> Watch | None:
        return self.watches.get(watch_id)

    async def list_all(self) -> list[Watch]:
        return list(self.watches.values())

    async def write(self, watch: Watch, *, expected_version: int | None, cap: int) -> Watch:
        """Insert (``expected_version`` None) or CAS-update; an activation counts the cap."""
        async with self._lock:
            current = self.watches.get(watch.id)
            if expected_version is None:
                if current is not None:
                    return current  # a replayed approval returns the committed watch
            elif current is None or current.version != expected_version:
                raise WatchConflict(watch.id)
            activating = watch.state is WatchState.ACTIVE and (
                current is None or current.state is not WatchState.ACTIVE
            )
            if activating:
                active = sum(
                    1
                    for other in self.watches.values()
                    if other.id != watch.id and other.state is WatchState.ACTIVE
                )
                if active >= cap:
                    raise CapExceeded(f"at most {cap} watches may be active")
            self.watches[watch.id] = replace(watch)
            return watch
