"""The keep-both store on real Postgres: upsert, load, and the fail-open read (bu-5ii4gj.11 R2a)."""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from butlers.api.read_models.calendar_workspace import (
    load_conflict_acknowledgements,
    record_conflict_acknowledgement,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def store(db):
    pool = await db("core")
    await pool.execute("TRUNCATE TABLE public.calendar_conflict_acknowledgements")
    manager = SimpleNamespace(
        butlers_with_module=lambda module: ["general"],
        butler_names=["general"],
        pool=lambda name: pool,
    )
    return manager, pool


async def test_a_recorded_acknowledgement_loads_with_both_digests(store) -> None:
    db, _ = store

    assert await record_conflict_acknowledgement(
        db, pair_id="pair-1", issue_digest="i1", state_digest="s1", option_id="keep_both"
    )

    assert await load_conflict_acknowledgements(db) == {"pair-1": ("i1", "s1")}


async def test_recording_again_replaces_the_digests_and_keeps_one_row(store) -> None:
    db, pool = store
    await record_conflict_acknowledgement(
        db, pair_id="pair-1", issue_digest="i1", state_digest="s1", option_id="keep_both"
    )

    await record_conflict_acknowledgement(
        db, pair_id="pair-1", issue_digest="i2", state_digest="s2", option_id="keep_both"
    )

    assert await load_conflict_acknowledgements(db) == {"pair-1": ("i2", "s2")}
    assert (
        await pool.fetchval("SELECT count(*) FROM public.calendar_conflict_acknowledgements") == 1
    )
    stamped = await pool.fetchval(
        "SELECT acknowledged_at >= now() - interval '1 minute' "
        "FROM public.calendar_conflict_acknowledgements"
    )
    assert stamped


async def test_pairs_are_independent(store) -> None:
    db, _ = store
    for pair in ("a", "b"):
        await record_conflict_acknowledgement(
            db, pair_id=pair, issue_digest=f"i-{pair}", state_digest=f"s-{pair}", option_id="k"
        )

    assert await load_conflict_acknowledgements(db) == {"a": ("i-a", "s-a"), "b": ("i-b", "s-b")}


async def test_an_unreadable_store_loads_nothing_instead_of_raising(store) -> None:
    db, pool = store
    await pool.execute("ALTER TABLE public.calendar_conflict_acknowledgements RENAME TO gone")
    try:
        assert await load_conflict_acknowledgements(db) == {}
    finally:
        await pool.execute("ALTER TABLE public.gone RENAME TO calendar_conflict_acknowledgements")
