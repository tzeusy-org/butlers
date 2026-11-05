"""Shared harness for the calendar overlay / prep contribution job tests.

The overlay and prep jobs read their domain sources with ``pool.fetch`` in a
fixed order, then write envelopes through the state store. Tests queue those
reads on the strict kit pool (:func:`source_reads`; SQL text is never inspected)
and capture state-store writes in memory (:class:`ContributionStateStore`).
SQL filtering and ordering are proven against real Postgres where they matter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

from butlers.testing.fake_pool import FakePool, fake_pool
from butlers.testing.records import records


class ContributionStateStore:
    """In-memory ``state_set`` / ``state_list`` / ``state_delete`` capture."""

    def __init__(self, seed: Mapping[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = dict(seed or {})
        self.deleted: list[str] = []
        self.set_calls: list[str] = []

    async def state_set(self, pool: Any, key: str, value: Any) -> int:
        self.store[key] = value
        self.set_calls.append(key)
        return 1

    async def state_list(self, pool: Any, prefix: str | None = None, keys_only: bool = True):
        if prefix is None:
            return sorted(self.store)
        return sorted(k for k in self.store if k.startswith(prefix))

    async def state_delete(self, pool: Any, key: str) -> None:
        self.store.pop(key, None)
        self.deleted.append(key)


@contextmanager
def patch_state(module: str, state: ContributionStateStore) -> Iterator[None]:
    """Route *module*'s state-store helpers to *state*."""
    with patch.multiple(
        module,
        state_set=state.state_set,
        state_list=state.state_list,
        state_delete=state.state_delete,
    ):
        yield


def source_reads(*results: list[Mapping[str, Any]] | BaseException) -> FakePool:
    """A strict pool whose successive ``fetch`` calls answer *results* in order.

    A row list answers as records; an exception instance is raised by that read.
    """
    queued = [r if isinstance(r, BaseException) else records(r) for r in results]
    return fake_pool(fetch=AsyncMock(side_effect=queued))


async def run_overlay_job(
    job: Callable[[Any, Any], Awaitable[dict[str, Any]]],
    *,
    first: list[Mapping[str, Any]] | None = None,
    second: list[Mapping[str, Any]] | None = None,
    seed: Mapping[str, Any] | None = None,
    today: date,
) -> tuple[dict[str, Any], ContributionStateStore, FakePool]:
    """Run an overlay *job* whose two source reads answer *first* then *second*."""
    module = "butlers.jobs.calendar_overlay"
    pool = source_reads(first or [], second or [])
    state = ContributionStateStore(seed=seed)
    with patch(f"{module}.today_sgt", return_value=today), patch_state(module, state):
        result = await job(pool, None)
    return result, state, pool
