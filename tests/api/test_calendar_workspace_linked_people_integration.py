"""Real-Postgres regression for the linked-people resolution JOIN (bu-qs64f).

The workspace read hydrates ``UnifiedCalendarEntry.linked_people`` for existing
events by batch-joining the per-schema ``calendar_event_entities`` junction to
the shared ``public.entities`` table (``_ENTRY_PEOPLE_SQL`` in
``api/read_models/calendar_workspace_v1.py``).

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

from butlers.api.read_models.calendar_workspace_v1 import _ENTRY_PEOPLE_SQL

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_SETUP_SQL = """
CREATE TABLE public.entities (
    id UUID PRIMARY KEY,
    canonical_name TEXT
);
CREATE TABLE calendar_event_entities (
    event_id UUID NOT NULL,
    entity_id UUID NOT NULL,
    PRIMARY KEY (event_id, entity_id)
);
"""


async def test_entry_people_sql_resolves_names_for_listed_events(provisioned_postgres_pool):
    """``$1::uuid[]`` scoping + ``public.entities`` join resolve linked names."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SETUP_SQL)

        ada, grace = uuid.uuid4(), uuid.uuid4()
        event_a, event_b, event_other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await pool.executemany(
            "INSERT INTO public.entities (id, canonical_name) VALUES ($1, $2)",
            [(ada, "Ada Lovelace"), (grace, "Grace Hopper")],
        )
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


async def test_entry_people_sql_keeps_link_with_missing_entity(provisioned_postgres_pool):
    """A link to an absent entity still returns a row (LEFT JOIN → NULL name)."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute(_SETUP_SQL)

        event_id = uuid.uuid4()
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
