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
from butlers.jobs.home_away_safety import ESCALATION_ADVANCE as AWAY_LADDER
from butlers.jobs.home_away_safety import INITIAL_GRACE_SECONDS as AWAY_GRACE
from butlers.jobs.home_away_safety import MARKERS as AWAY_MARKERS
from butlers.jobs.home_safety import ESCALATION_ADVANCE as SAFETY_LADDER

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


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
    db_pool: asyncpg.Pool,
) -> None:
    safety = await _gaps_between_escalations(db_pool, escalation_advance=SAFETY_LADDER)
    default = await _gaps_between_escalations(db_pool)

    assert safety == [timedelta(hours=1), timedelta(hours=4), timedelta(days=1), timedelta(days=1)]
    assert default == [timedelta(days=1), timedelta(days=3), timedelta(days=7), timedelta(days=7)]


async def test_away_safety_ladder_drives_each_level(db_pool: asyncpg.Pool) -> None:
    """bu-jv1moa.87: the away sources' opt-in, 4 hours after the first rung then daily."""
    away = await _gaps_between_escalations(db_pool, escalation_advance=AWAY_LADDER)

    assert away == [timedelta(hours=4), timedelta(days=1), timedelta(days=1), timedelta(days=1)]


@pytest.mark.parametrize("source", ["home:freezer", "home:away-door", "home:sensing-blind"])
async def test_away_safety_delivery_markers_survive_a_concurrent_confirmation(
    db_pool: asyncpg.Pool, source: str
) -> None:
    """A run that confirms with metadata built before the send never erases a marker."""
    fp = compute_fingerprint(source, 1, {"id": uuid.uuid4().hex})
    markers = {
        "notified_at": "2026-11-14T08:00:00+00:00",
        "rung_due_at": "2026-11-14T09:00:00+00:00",
        "rung_notified_at": "2026-11-14T09:00:05+00:00",
    }

    async def check_in(metadata: dict, *, confirm_only: bool) -> None:
        await reconcile_snapshot(
            db_pool,
            source=source,
            observations=[
                Observation(
                    fingerprint=fp, summary="s", metadata=metadata, confirm_only=confirm_only
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=AWAY_GRACE,
            escalation_advance=AWAY_LADDER,
            preserve_metadata_keys=AWAY_MARKERS,
        )

    await check_in({"entity_id": "x"}, confirm_only=False)
    await check_in({"entity_id": "x", **markers}, confirm_only=True)
    await check_in({"entity_id": "x", "detected_since": "earlier"}, confirm_only=True)
    active = await get_active_condition(db_pool, source=source, fingerprint=fp)

    assert {key: active["metadata"].get(key) for key in markers} == markers
    assert active["metadata"]["detected_since"] == "earlier"


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


async def test_a_preserved_key_survives_a_confirmation_that_lacks_it(db_pool: asyncpg.Pool) -> None:
    metadata = await _confirm_after_a_marker(db_pool, preserve_metadata_keys=("notified_at",))

    assert metadata["notified_at"] == "2026-10-18T12:00:00+00:00"
    assert metadata["detected_since"] == "earlier"


async def test_other_producers_keep_replace_semantics(db_pool: asyncpg.Pool) -> None:
    metadata = await _confirm_after_a_marker(db_pool)

    assert "notified_at" not in metadata
    assert metadata["detected_since"] == "earlier"


async def test_a_preserved_key_the_confirmation_carries_wins(db_pool: asyncpg.Pool) -> None:
    source = f"test:preserve-win-{uuid.uuid4().hex}"
    fp = compute_fingerprint(source, 1, {"id": "x"})
    for stamp in ("first", "second"):
        await reconcile_snapshot(
            db_pool,
            source=source,
            observations=[
                Observation(fingerprint=fp, summary="s", metadata={"notified_at": stamp})
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
            preserve_metadata_keys=("notified_at",),
        )

    active = await get_active_condition(db_pool, source=source, fingerprint=fp)
    assert active["metadata"]["notified_at"] == "second"


@pytest.mark.parametrize("identity_version", [None, 1], ids=["plain", "identity-payload"])
@pytest.mark.parametrize("opt_ins", [{}, {"preserve_metadata_keys": ("notified_at",)}])
async def test_a_confirmation_replaces_non_object_stored_metadata(
    db_pool: asyncpg.Pool, opt_ins: dict, identity_version: int | None
) -> None:
    """Double-encoded (string) metadata must not make a confirmation fail (main replaces it)."""
    source = f"test:string-meta-{uuid.uuid4().hex}"
    fp = compute_fingerprint(source, 1, {"id": "x"})

    async def check_in() -> None:
        await reconcile_snapshot(
            db_pool,
            source=source,
            observations=[
                Observation(
                    fingerprint=fp,
                    summary="s",
                    metadata={"entity_id": "x"},
                    identity_version=identity_version,
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
            **opt_ins,
        )

    await check_in()
    await db_pool.execute(
        "UPDATE public.owner_conditions SET metadata = to_jsonb('double-encoded'::text) "
        "WHERE source = $1 AND state IN ('open', 'aging')",
        source,
    )
    await check_in()

    active = await get_active_condition(db_pool, source=source, fingerprint=fp)
    expected = {"entity_id": "x"}
    if identity_version is not None:
        expected["identity_payload"] = {"version": identity_version}
    assert active["metadata"] == expected
