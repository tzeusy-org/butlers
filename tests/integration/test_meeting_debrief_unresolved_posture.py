"""Real-Postgres: a debrief never names an attendee who later resolves to an inactive entity.

The snapshot keeps an attendee that resolved to nothing at snapshot time by raw name and email.
When that email later belongs to a ``no_contact`` entity, ``meeting_debrief_pending`` must not
name them; an attendee that stays unresolved is still shown (bu-yqxbc8).
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.meeting_debrief import meeting_debrief_pending

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_TRUNCATE = ["public.entities", "relationship.meeting_debriefs"]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "memory", "relationship"]
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=1,
        init=register_jsonb_codec,
        server_settings={"search_path": "relationship, public"},
    )
    for table in _TRUNCATE:
        await p.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    yield p
    await p.close()


async def _snapshot(pool, *attendees: dict) -> None:
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO relationship.meeting_debriefs
            (event_id, occurrence_start, occurrence_end, event_title, attendees,
             state, prompted_at, batch_position)
        VALUES ($1, $2, $2, 'Planning', $3::text::jsonb, 'pending', $2, 1)
        """,
        uuid.uuid4(),
        now,
        json.dumps(list(attendees)),
    )


async def _later_person(pool, email: str, posture: str) -> None:
    # A distinct name per call: uq_entities_canonical_type_live allows one live person per
    # (canonical_name, entity_type), and the shared-email cases need several live claimants.
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        f"Resolved Later {uuid.uuid4().hex[:8]}",
    )
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, 'has-email', $2, 'literal', 'test')",
        entity_id,
        email,
    )
    await pool.execute("UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture)


def _names(pending: dict) -> list[str]:
    return [a["email"] for d in pending["debriefs"] for a in d["attendees"]]


async def test_attendee_unresolved_at_snapshot_and_later_no_contact_is_not_named(pool) -> None:
    await _snapshot(
        pool,
        {"entity_id": None, "name": "Gone Away", "email": "gone@example.test"},
        {"entity_id": None, "name": "Still Unknown", "email": "unknown@example.test"},
    )
    await _later_person(pool, "gone@example.test", "no_contact")

    assert _names(await meeting_debrief_pending(pool)) == ["unknown@example.test"]


async def test_attendee_unresolved_at_snapshot_and_later_active_is_still_named(pool) -> None:
    await _snapshot(pool, {"entity_id": None, "name": "Friend", "email": "friend@example.test"})
    await _later_person(pool, "friend@example.test", "active")

    assert _names(await meeting_debrief_pending(pool)) == ["friend@example.test"]


@pytest.mark.parametrize("blocking_first", [True, False], ids=["blocker-first", "blocker-last"])
async def test_shared_email_with_a_no_contact_claimant_is_withheld_in_either_row_order(
    pool, blocking_first
) -> None:
    await _snapshot(pool, {"entity_id": None, "name": "Shared", "email": "shared@example.test"})
    order = ["no_contact", "active"] if blocking_first else ["active", "no_contact"]
    for posture in order:
        await _later_person(pool, "shared@example.test", posture)

    assert _names(await meeting_debrief_pending(pool)) == []


async def _entity_with_email(pool, email: str, *, posture="active", metadata=None) -> str:
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ('Dana', 'person', $1::text::jsonb) RETURNING id",
        json.dumps(metadata or {}),
    )
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1, 'has-email', $2, 'literal', 'test')",
        entity_id,
        email,
    )
    await pool.execute("UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture)
    return str(entity_id)


async def test_one_entity_with_two_casings_of_its_email_resolves_to_that_entity(pool) -> None:
    from butlers.jobs.meeting_debrief import _resolve_people

    dana = await _entity_with_email(pool, "Dana@Example.com")
    await pool.execute(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src) "
        "VALUES ($1::uuid, 'has-email', 'dana@example.com', 'literal', 'test')",
        dana,
    )

    people = await _resolve_people(pool, {"dana@example.com"})

    assert people["dana@example.com"]["entity_id"] == dana


_TOMBSTONES = [{"merged_into": str(uuid.uuid4())}, {"deleted_at": "2031-01-01"}]


@pytest.mark.parametrize("metadata", _TOMBSTONES, ids=["merged", "deleted"])
async def test_tombstoned_active_holder_neither_blocks_nor_makes_the_email_ambiguous(
    pool, metadata
) -> None:
    from butlers.jobs.meeting_debrief import _resolve_people

    live = await _entity_with_email(pool, "dana@example.com")
    await _entity_with_email(pool, "dana@example.com", metadata=metadata)

    people = await _resolve_people(pool, {"dana@example.com"})

    assert people["dana@example.com"]["entity_id"] == live
    assert people["dana@example.com"]["posture"] == "active"


@pytest.mark.parametrize("metadata", _TOMBSTONES, ids=["merged", "deleted"])
async def test_tombstoned_no_contact_holder_still_withholds_beside_a_live_active_entity(
    pool, metadata
) -> None:
    await _snapshot(pool, {"entity_id": None, "name": "Dana", "email": "dana@example.com"})
    await _entity_with_email(pool, "dana@example.com")
    await _entity_with_email(pool, "dana@example.com", posture="no_contact", metadata=metadata)

    assert _names(await meeting_debrief_pending(pool)) == []


async def test_deleted_memorial_holder_alone_still_withholds(pool) -> None:
    await _snapshot(pool, {"entity_id": None, "name": "Dana", "email": "dana@example.com"})
    await _entity_with_email(
        pool, "dana@example.com", posture="memorial", metadata={"deleted_at": "2031-01-01"}
    )

    assert _names(await meeting_debrief_pending(pool)) == []
