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
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ('Resolved Later', 'person') RETURNING id"
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
