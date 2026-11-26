"""Shared harness for the calendar overlay / prep contribution job tests.

The overlay and prep jobs read their domain sources with ``pool.fetch`` in a
fixed order, then write envelopes through the state store. Tests queue those
reads on the strict kit pool (:func:`source_reads`; SQL text is never inspected)
and capture state-store writes in memory (:class:`ContributionStateStore`).
SQL filtering and ordering are proven against real Postgres where they matter.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

from butlers.testing.briefing_clock import sgt_noon
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

    async def state_get(self, pool: Any, key: str) -> Any:
        return self.store.get(key)

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
    patches = {
        "state_set": state.state_set,
        "state_list": state.state_list,
        "state_delete": state.state_delete,
    }
    # Only modules that import ``state_get`` have one to route (the prep jobs do not).
    if hasattr(importlib.import_module(module), "state_get"):
        patches["state_get"] = state.state_get
    with patch.multiple(module, **patches):
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
    extra: tuple[list[Mapping[str, Any]], ...] = (),
    owner_zone: str | BaseException | None = "Asia/Singapore",
) -> tuple[dict[str, Any], ContributionStateStore, FakePool]:
    """Run an overlay *job* whose source reads answer *first*, *second*, then *extra* in order.

    *owner_zone* is the saved owner timezone the run resolves: a zone name, None for "never saved",
    or an exception for a failed read.
    """
    module = "butlers.jobs.calendar_overlay"
    pool = source_reads(first or [], second or [], *extra)
    state = ContributionStateStore(seed=seed)
    with (
        patch("butlers.jobs.briefing._now", return_value=sgt_noon(today)),
        patch(
            "butlers.jobs.briefing.load_configured_general_timezone",
            AsyncMock(side_effect=owner_zone)
            if isinstance(owner_zone, BaseException)
            else AsyncMock(return_value=owner_zone),
        ),
        patch_state(module, state),
    ):
        result = await job(pool, None)
    return result, state, pool


def save_owner_zone(monkeypatch: Any, name: str | BaseException | None) -> None:
    """The owner's saved timezone a run resolves (None: never saved; exception: read failed)."""

    async def load(pool: Any) -> str | None:
        if isinstance(name, BaseException):
            raise name
        return name

    monkeypatch.setattr("butlers.jobs.briefing.load_configured_general_timezone", load)
