"""Store-level contract every ``WatchStore`` must pass (memory and PostgreSQL)."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from butlers.watches import CapExceeded, WatchConflict, WatchState
from butlers.watches.model import LIFETIME, Watch

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
PREDICATE = {"tag": "bundle", "bundle_id": "delay-commitment"}


def _hex(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def make_watch(watch_id: str, *, state: WatchState = WatchState.ACTIVE, **changes) -> Watch:
    watch = Watch(
        id=watch_id,
        version=1,
        generation=1,
        predicate=PREDICATE,
        predicate_digest=_hex("predicate"),
        grant_digest=_hex("grant"),
        sources=("bundle:delay-commitment",),
        categories=("routine",),
        delivery="digest",
        state=state,
        created_at=NOW,
        expires_at=NOW + LIFETIME,
        approval_digest=_hex(f"card:{watch_id}"),
    )
    return replace(watch, **changes)


class WatchStoreContract:
    def make_store(self):
        raise NotImplementedError

    async def test_insert_then_get_round_trips(self):
        store = self.make_store()
        watch = make_watch("w1")
        assert await store.write(watch, expected_version=None, cap=50) == watch
        assert await store.get("w1") == watch
        assert await store.get("missing") is None
        assert [w.id for w in await store.list_all()] == ["w1"]

    async def test_a_replayed_insert_returns_the_committed_watch_unchanged(self):
        store = self.make_store()
        first = make_watch("w1")
        await store.write(first, expected_version=None, cap=50)
        other = replace(first, delivery="right_away", approval_digest=_hex("other"))
        assert await store.write(other, expected_version=None, cap=50) == first
        assert (await store.get("w1")) == first

    async def test_update_is_a_version_cas(self):
        store = self.make_store()
        first = make_watch("w1")
        await store.write(first, expected_version=None, cap=50)
        second = replace(first, version=2, delivery="right_away")
        await store.write(second, expected_version=1, cap=50)
        assert (await store.get("w1")) == second
        with pytest.raises(WatchConflict):
            await store.write(replace(second, version=3), expected_version=1, cap=50)
        with pytest.raises(WatchConflict):
            await store.write(make_watch("ghost", version=2), expected_version=1, cap=50)

    async def test_the_cap_counts_active_watches_only(self):
        store = self.make_store()
        await store.write(make_watch("a"), expected_version=None, cap=2)
        await store.write(make_watch("b"), expected_version=None, cap=2)
        with pytest.raises(CapExceeded):
            await store.write(make_watch("c"), expected_version=None, cap=2)
        paused = make_watch("a", version=2, state=WatchState.PAUSED)
        await store.write(paused, expected_version=1, cap=2)
        await store.write(make_watch("c"), expected_version=None, cap=2)
        with pytest.raises(CapExceeded):  # resuming counts against the cap
            await store.write(
                make_watch("a", version=3, state=WatchState.ACTIVE),
                expected_version=2,
                cap=2,
            )

    async def test_concurrent_activations_cannot_exceed_the_cap(self):
        store = self.make_store()
        results = await asyncio.gather(
            *(store.write(make_watch(f"w{i}"), expected_version=None, cap=3) for i in range(8)),
            return_exceptions=True,
        )
        assert sum(isinstance(r, CapExceeded) for r in results) == 5
        active = [w for w in await store.list_all() if w.state is WatchState.ACTIVE]
        assert len(active) == 3

    async def test_a_renewal_keeps_the_boundary_and_moves_expiry_forward(self):
        store = self.make_store()
        first = make_watch("w1")
        await store.write(first, expected_version=None, cap=50)
        renewed = replace(
            first, version=2, generation=2, expires_at=first.expires_at + timedelta(days=30)
        )
        await store.write(renewed, expected_version=1, cap=50)
        assert (await store.get("w1")) == renewed

    async def test_serialized_excludes_a_second_holder_of_the_same_watch(self):
        store = self.make_store()
        inside, overlap = 0, 0

        async def hold(watch_id):
            nonlocal inside, overlap
            async with store.serialized(watch_id):
                inside += 1
                overlap = max(overlap, inside)
                await asyncio.sleep(0.02)
                inside -= 1

        await asyncio.gather(hold("w1"), hold("w1"), hold("w1"))
        assert overlap == 1

    async def test_serialized_lets_different_watches_proceed_together(self):
        store = self.make_store()
        arrived = asyncio.Barrier(2)

        async def hold(watch_id):
            async with store.serialized(watch_id):
                await asyncio.wait_for(arrived.wait(), timeout=5)

        await asyncio.gather(hold("w1"), hold("w2"))

    async def test_serialized_releases_after_an_error(self):
        store = self.make_store()
        with pytest.raises(RuntimeError):
            async with store.serialized("w1"):
                raise RuntimeError("boom")
        async with store.serialized("w1"):
            pass
