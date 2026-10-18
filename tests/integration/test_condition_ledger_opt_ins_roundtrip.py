"""Real-Postgres regression: the ledger's opt-in cadence and metadata preservation (bu-jv1moa.18).

``escalation_advance`` gives one producer its own re-escalation ladder and
``preserve_metadata_keys`` keeps a named metadata key across a confirmation. Neither
may change the shared behavior of a producer that omits them.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import timedelta

import asyncpg
import pytest

from butlers.core.owner_conditions import (
    Observation,
    compute_fingerprint,
    get_active_condition,
    reconcile_snapshot,
)
from butlers.db import register_jsonb_codec
from butlers.jobs.home_safety import ESCALATION_ADVANCE as SAFETY_LADDER
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=2, init=register_jsonb_codec
    )
    yield p
    await p.close()


async def _gaps_between_escalations(pool: asyncpg.Pool, **opt_ins) -> list[timedelta]:
    """Open one condition, force it due four times, and return each next-due gap."""
    source = f"test:ladder-{uuid.uuid4().hex}"
    fp = compute_fingerprint(source, 1, {"id": "x"})
    await reconcile_snapshot(
        pool,
        source=source,
        observations=[Observation(fingerprint=fp, summary="s")],
        snapshot_complete=False,
        initial_grace_seconds=0,
        **opt_ins,
    )
    gaps: list[timedelta] = []
    for _ in range(4):
        await pool.execute(
            "UPDATE public.owner_conditions SET next_reescalate_at = now() "
            "WHERE source = $1 AND state IN ('open', 'aging')",
            source,
        )
        (transition,) = await reconcile_snapshot(
            pool,
            source=source,
            observations=[Observation(fingerprint=fp, summary="s")],
            snapshot_complete=False,
            initial_grace_seconds=0,
            **opt_ins,
        )
        assert transition.transition == "escalation_due"
        row = await pool.fetchrow(
            "SELECT next_reescalate_at - last_escalated_at AS gap FROM public.owner_conditions "
            "WHERE source = $1 AND state IN ('open', 'aging')",
            source,
        )
        gaps.append(row["gap"])
    return gaps


async def test_safety_ladder_drives_each_level_and_the_default_is_unchanged(
    pool: asyncpg.Pool,
) -> None:
    safety = await _gaps_between_escalations(pool, escalation_advance=SAFETY_LADDER)
    default = await _gaps_between_escalations(pool)

    assert safety == [timedelta(hours=1), timedelta(hours=4), timedelta(days=1), timedelta(days=1)]
    assert default == [timedelta(days=1), timedelta(days=3), timedelta(days=7), timedelta(days=7)]


async def _confirm_after_a_marker(pool: asyncpg.Pool, **opt_ins) -> dict:
    source = f"test:preserve-{uuid.uuid4().hex}"
    fp = compute_fingerprint(source, 1, {"id": "x"})

    async def check_in(metadata: dict, *, confirm_only: bool) -> None:
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[
                Observation(
                    fingerprint=fp, summary="s", metadata=metadata, confirm_only=confirm_only
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
            **opt_ins,
        )

    await check_in({"entity_id": "x"}, confirm_only=False)
    # The delivery path records its marker after the send...
    await check_in(
        {"entity_id": "x", "notified_at": "2026-10-18T12:00:00+00:00"}, confirm_only=True
    )
    # ...and a concurrent reconcile confirms with metadata built before it.
    await check_in({"entity_id": "x", "detected_since": "earlier"}, confirm_only=True)
    active = await get_active_condition(pool, source=source, fingerprint=fp)
    return active["metadata"]


async def test_a_preserved_key_survives_a_confirmation_that_lacks_it(pool: asyncpg.Pool) -> None:
    metadata = await _confirm_after_a_marker(pool, preserve_metadata_keys=("notified_at",))

    assert metadata["notified_at"] == "2026-10-18T12:00:00+00:00"
    assert metadata["detected_since"] == "earlier"


async def test_other_producers_keep_replace_semantics(pool: asyncpg.Pool) -> None:
    metadata = await _confirm_after_a_marker(pool)

    assert "notified_at" not in metadata
    assert metadata["detected_since"] == "earlier"


async def test_a_preserved_key_the_confirmation_carries_wins(pool: asyncpg.Pool) -> None:
    source = f"test:preserve-win-{uuid.uuid4().hex}"
    fp = compute_fingerprint(source, 1, {"id": "x"})
    for stamp in ("first", "second"):
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[
                Observation(fingerprint=fp, summary="s", metadata={"notified_at": stamp})
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
            preserve_metadata_keys=("notified_at",),
        )

    active = await get_active_condition(pool, source=source, fingerprint=fp)
    assert active["metadata"]["notified_at"] == "second"
