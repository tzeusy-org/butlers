"""REQ-owner-watches-004: an owner `never` on a watch notice pauses the watch, never deletes it."""

from __future__ import annotations

import json
from typing import Any

import pytest

from butlers.conditions import ConditionEvaluator
from butlers.conditions.memory import InMemoryConditionStore
from butlers.watches import WatchService, WatchState
from butlers.watches import never as never_mod
from butlers.watches.memory import InMemoryWatchStore
from tests.watches.test_watch_runner import NOW, grant_for, make_watch

INSIGHT = "5b6f0d52-0000-4000-8000-000000000001"


class Pool:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args: Any):
        self.queries.append((sql, args))
        return self.row


def candidate(watch_id: str, **changes) -> dict[str, Any]:
    row = {
        "origin_butler": "switchboard",
        "category": "owner_watch",
        "dedup_key": f"owner_watch:{watch_id}:g1",
        "metadata": json.dumps({"watch_id": watch_id, "generation": 1}),
    }
    return {**row, **changes}


@pytest.fixture
def service():
    return WatchService(
        InMemoryWatchStore(), ConditionEvaluator(InMemoryConditionStore()), grant_for
    )


async def run(pool, service, *, origin="owner"):
    return await never_mod.pause_on_never(
        pool, INSIGHT, origin=origin, service_for=lambda: service, now=NOW
    )


async def test_an_owner_never_pauses_the_watch_and_keeps_it(service):
    watch = await make_watch(service)
    assert (await run(Pool(candidate(watch.id)), service))["status"] == "paused"
    stored = await service.store.get(watch.id)
    assert stored is not None and stored.state is WatchState.PAUSED


async def test_a_replayed_never_is_harmless(service):
    watch = await make_watch(service)
    pool = Pool(candidate(watch.id))
    await run(pool, service)
    assert (await run(pool, service))["status"] == "paused"


@pytest.mark.parametrize("origin", ["model", "connector", "", "system"])
async def test_only_the_owner_pauses(service, origin):
    watch = await make_watch(service)
    result = await run(Pool(candidate(watch.id)), service, origin=origin)
    assert result == {"status": "ignored", "reason": "not_owner"}
    assert (await service.store.get(watch.id)).state is WatchState.ACTIVE


async def test_another_insight_is_not_touched(service):
    watch = await make_watch(service)
    for change in ({"category": "birthday"}, {"origin_butler": "relationship"}):
        result = await run(Pool(candidate(watch.id, **change)), service)
        assert result["status"] == "ignored"
    assert (await service.store.get(watch.id)).state is WatchState.ACTIVE


async def test_a_candidate_whose_key_and_metadata_disagree_pauses_nothing(service):
    watch = await make_watch(service)
    other = await make_watch(service, delivery="right_away")
    row = candidate(other.id, dedup_key=f"owner_watch:{watch.id}:g1")
    assert (await run(Pool(row), service))["status"] == "ignored"
    assert (await service.store.get(watch.id)).state is WatchState.ACTIVE
    assert (await service.store.get(other.id)).state is WatchState.ACTIVE


async def test_an_unknown_insight_or_watch_is_ignored(service):
    assert (await run(Pool(None), service))["status"] == "ignored"
    gone = candidate("11111111-1111-4111-8111-111111111111")
    assert (await run(Pool(gone), service))["status"] == "ignored"


async def test_a_closed_watch_stays_closed(service):
    watch = await make_watch(service)
    card = await service.propose_delete(watch.id)
    from butlers.conditions import Snapshot
    from butlers.watches import OwnerApproval

    await service.approve(card, OwnerApproval(card.digest, "owner"), Snapshot(now=NOW))
    assert (await run(Pool(candidate(watch.id)), service))["status"] == "already_closed"


async def test_without_a_service_it_reports_unavailable_and_changes_nothing():
    pool = Pool(candidate("11111111-1111-4111-8111-111111111111"))
    result = await never_mod.pause_on_never(
        pool, INSIGHT, origin="owner", service_for=lambda: None, now=NOW
    )
    assert result == {"status": "unavailable"}
