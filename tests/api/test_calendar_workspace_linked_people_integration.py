"""Real-Postgres regression for the linked-people resolution JOIN (bu-qs64f).

The workspace read hydrates ``UnifiedCalendarEntry.linked_people`` for existing
events by batch-joining the per-schema ``calendar_event_entities`` junction to
the shared ``public.entities`` table (``_ENTRY_PEOPLE_SQL`` in
``api/read_models/calendar_workspace.py``).

The mocked-pool unit tests in ``tests/api/test_calendar_workspace.py`` stub
``fan_out``/``fan_out_with_status`` and never round-trip through asyncpg, so they
cannot catch the class of bug this SQL is exposed to — the ``$1::uuid[]`` array
bind, the cross-schema ``public.entities`` join, and the ``LEFT JOIN`` NULL
label case (a link to a tombstoned/absent entity). These tests run the exact
production SQL against a real Postgres instance (testcontainers) to prove:

1. Passing an event-id array via ``$1::uuid[]`` matches only the listed events
   and resolves each link's ``canonical_name`` from ``public.entities``, ordered
   deterministically for stable avatar rendering.
2. A link whose entity row is missing (``LEFT JOIN`` → NULL name) still returns
   a row (so the person is never silently dropped) — the resolver maps the NULL
   to the ``"Unknown"`` label.
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from butlers.api.read_models.calendar_workspace import _ENTRY_PEOPLE_SQL

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def _events(pool, count: int) -> list[uuid.UUID]:
    source = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, provider, calendar_id, "
        "display_name, writable, metadata) VALUES ('provider:google:primary', 'provider_event', "
        "'user', 'google', 'primary', 'Primary', true, '{}') RETURNING id"
    )
    return [
        await pool.fetchval(
            "INSERT INTO calendar_events (source_id, origin_ref, title, timezone, starts_at, "
            "ends_at, status, metadata, source_butler) VALUES ($1, $2, 'x', 'UTC', "
            "now(), now() + interval '1 hour', 'confirmed', '{}', 'general') RETURNING id",
            source,
            f"evt-{n}",
        )
        for n in range(count)
    ]


async def test_entry_people_sql_resolves_names_for_listed_events(db):
    """``$1::uuid[]`` scoping + ``public.entities`` join resolve linked names."""
    pool = await db("core")
    event_a, event_b, event_other = await _events(pool, 3)
    ada, grace = [
        await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            name,
        )
        for name in ("Ada Lovelace", "Grace Hopper")
    ]
    await pool.executemany(
        "INSERT INTO calendar_event_entities (event_id, entity_id) VALUES ($1, $2)",
        [
            (event_a, grace),  # deliberately inserted out of name order
            (event_a, ada),
            (event_b, ada),
            (event_other, ada),  # NOT in the requested id array
        ],
    )

    rows = await pool.fetch(_ENTRY_PEOPLE_SQL, [event_a, event_b])

    # event_other is excluded by the ``$1::uuid[]`` scoping.
    assert {r["event_id"] for r in rows} == {event_a, event_b}
    # event_a's two links come back ordered by lower(canonical_name):
    # Ada before Grace, regardless of insertion order.
    event_a_names = [r["canonical_name"] for r in rows if r["event_id"] == event_a]
    assert event_a_names == ["Ada Lovelace", "Grace Hopper"]


async def test_entry_people_sql_keeps_link_with_missing_entity(db):
    """A link to an absent entity still returns a row (LEFT JOIN → NULL name)."""
    pool = await db("core")
    (event_id,) = await _events(pool, 1)
    # The real FK would cascade the link away with its entity; drop it to model a link
    # whose entity row is unreadable or gone.
    await pool.execute(
        "ALTER TABLE calendar_event_entities DROP CONSTRAINT calendar_event_entities_entity_id_fkey"
    )
    ghost = uuid.uuid4()  # never inserted into public.entities
    await pool.execute(
        "INSERT INTO calendar_event_entities (event_id, entity_id) VALUES ($1, $2)",
        event_id,
        ghost,
    )

    rows = await pool.fetch(_ENTRY_PEOPLE_SQL, [event_id])

    assert len(rows) == 1
    assert rows[0]["event_id"] == event_id
    assert rows[0]["entity_id"] == ghost
    # The person is never silently dropped; the resolver maps this NULL to
    # the "Unknown" label.
    assert rows[0]["canonical_name"] is None
