"""In-memory ``WatchStore`` with the cap and version CAS the SQL store will enforce (dark)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from butlers.watches.model import CapExceeded, Watch, WatchConflict, WatchState


class InMemoryWatchStore:
    def __init__(self) -> None:
        self.watches: dict[str, Watch] = {}
        self._lock = asyncio.Lock()

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
