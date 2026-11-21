"""Real-PostgreSQL care duties at the Relationship seam (bu-s11n0s.13 S1)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.roster_imports import roster_module

care = roster_module("relationship", "tools/care_duties")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_ANCHOR = datetime(2026, 11, 24, 15, 15, tzinfo=UTC)  # a Tuesday
_WEEKLY = "FREQ=WEEKLY;BYDAY=TU"


@pytest.fixture
async def rel_pool(db) -> asyncpg.Pool:
    return await db("core", "memory", "relationship")


async def _entity(pool: asyncpg.Pool, name: str, entity_type: str = "person") -> object:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, $2) RETURNING id",
        name,
        entity_type,
    )


async def _duty(pool: asyncpg.Pool, subject: object, **over) -> dict:
    args = {
        "subject_entity_id": subject,
        "title": "Rex worming",
        "kind": "calendar_anchored",
        "rrule": _WEEKLY,
        "interval_days": None,
        "anchor_at": _ANCHOR,
    }
    args.update(over)
    return await care.create_duty(pool, **args)


async def test_a_pet_is_a_dependent_entity_with_a_duty(rel_pool: asyncpg.Pool) -> None:
    rex = await _entity(rel_pool, "Rex care test", "animal")
    duty = await _duty(rel_pool, rex)
    assert duty["subject_entity_id"] == rex
    assert [d["id"] for d in await care.list_duties(rel_pool)] == [duty["id"]]


async def test_assignment_is_idempotent_and_an_owner_reassignment_wins(
    rel_pool: asyncpg.Pool,
) -> None:
    mia = await _entity(rel_pool, "Mia care test")
    first, second = await _entity(rel_pool, "Carer one"), await _entity(rel_pool, "Carer two")
    duty = await _duty(rel_pool, mia, title="Mia pickup")
    start = _ANCHOR + timedelta(days=7)

    await care.assign_occurrence(rel_pool, duty["id"], start, carrier_entity_id=first)
    again = await care.assign_occurrence(rel_pool, duty["id"], start, carrier_entity_id=first)
    reassigned = await care.assign_occurrence(
        rel_pool, duty["id"], start, carrier_entity_id=second, state="assumed"
    )

    assert again["carrier_entity_id"] == first
    assert (reassigned["carrier_entity_id"], reassigned["state"]) == (second, "assumed")
    count = await rel_pool.fetchval(
        "SELECT count(*) FROM relationship.care_occurrences WHERE duty_id = $1", duty["id"]
    )
    assert count == 1


async def test_retiring_hides_a_duty_and_its_occurrences_but_keeps_history(
    rel_pool: asyncpg.Pool,
) -> None:
    mia = await _entity(rel_pool, "Mia retire test")
    carer = await _entity(rel_pool, "Carer retire test")
    duty = await _duty(rel_pool, mia, title="Mia swim")
    await care.assign_occurrence(rel_pool, duty["id"], _ANCHOR, carrier_entity_id=carer)
    window = {"start": _ANCHOR - timedelta(days=1), "end": _ANCHOR + timedelta(days=1)}
    assert len(await care.list_occurrences(rel_pool, **window)) == 1

    await care.retire_duty(rel_pool, duty["id"])
    await care.retire_duty(rel_pool, duty["id"])  # idempotent

    assert await care.list_occurrences(rel_pool, **window) == []
    assert duty["id"] not in [d["id"] for d in await care.list_duties(rel_pool)]
    assert duty["id"] in [d["id"] for d in await care.list_duties(rel_pool, include_retired=True)]
    with pytest.raises(care.CareDutyError):
        await care.assign_occurrence(rel_pool, duty["id"], _ANCHOR, carrier_entity_id=carer)


async def test_a_merged_entity_cannot_be_a_carer(rel_pool: asyncpg.Pool) -> None:
    mia = await _entity(rel_pool, "Mia merged test")
    gone = await _entity(rel_pool, "Merged carer")
    keeper = await _entity(rel_pool, "Merge target")
    await rel_pool.execute(
        "UPDATE public.entities SET metadata = metadata || "
        "jsonb_build_object('merged_into', $2::text) WHERE id = $1",
        gone,
        str(keeper),
    )
    duty = await _duty(rel_pool, mia, title="Mia dentist")
    with pytest.raises(care.CareDutyError) as exc:
        await care.assign_occurrence(rel_pool, duty["id"], _ANCHOR, carrier_entity_id=gone)
    assert exc.value.code == "not_found"


async def test_the_kind_shape_is_enforced_by_the_database(rel_pool: asyncpg.Pool) -> None:
    mia = await _entity(rel_pool, "Mia shape test")
    with pytest.raises(asyncpg.CheckViolationError):
        await rel_pool.execute(
            "INSERT INTO relationship.care_duties (subject_entity_id, title, kind, rrule, "
            "interval_days, anchor_at) VALUES ($1, 'x', 'calendar_anchored', $2, 7, now())",
            mia,
            _WEEKLY,
        )
    duty = await _duty(rel_pool, mia, title="Mia shape duty")
    with pytest.raises(asyncpg.CheckViolationError):
        await rel_pool.execute(
            "INSERT INTO relationship.care_occurrences (duty_id, occurrence_start, state) "
            "VALUES ($1, now(), 'covered')",
            duty["id"],
        )
