"""Tests for co-attended edge derivation in run_interaction_sync.

Validates that interaction_sync mints ``co-attended`` edges in
``relationship.entity_facts`` for entities that share a calendar event, and
that entities in *different* events do NOT receive cross-event edges.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime

import pytest

# All tables come from named-schema migrations; setup data uses real writers.
from roster.relationship.tests.calendar_projection import (
    link_identity,
    project_event,
)
from roster.relationship.tests.calendar_projection import (
    make_entity as _make_entity,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def _link_email(pool, *, entity_id: uuid.UUID, email: str) -> None:
    await link_identity(pool, entity_id=entity_id, predicate="has-email", value=email)


async def _insert_calendar_event(
    pool,
    *,
    title: str = "Team Sync",
    starts_at: datetime | None = None,
    attendees: list[dict] | None = None,
) -> str:
    return str(await project_event(pool, title=title, starts_at=starts_at, attendees=attendees))


async def _co_attended_edges(pool, entity_a: uuid.UUID, entity_b: uuid.UUID) -> list[dict]:
    """Return active co-attended entity_facts rows between entity_a and entity_b.

    subject is UUID; object is TEXT (entity UUID stored as string in entity_facts).
    """
    return await pool.fetch(
        """
        SELECT subject, predicate, object
        FROM relationship.entity_facts
        WHERE predicate = 'co-attended'
          AND validity = 'active'
          AND (
              (subject = $1 AND object = $2)
              OR (subject = $3 AND object = $4)
          )
        """,
        entity_a,
        str(entity_b),
        entity_b,
        str(entity_a),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.pg_clock
async def test_co_attended_edges_minted_for_shared_event(interaction_sync_pool):
    """Two attendees at the same event receive co-attended edges in both directions."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entity_a = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_a, email="alice@example.com")

        entity_b = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_b, email="bob@example.com")

        await _insert_calendar_event(
            pool,
            title="Team Meeting",
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["co_attended_edges_minted"] == 2  # A→B and B→A
        assert result["errors"] == 0

        edges = await _co_attended_edges(pool, entity_a, entity_b)
        assert len(edges) == 2
        subjects = {str(r["subject"]) for r in edges}
        assert str(entity_a) in subjects
        assert str(entity_b) in subjects


@pytest.mark.pg_clock
async def test_co_attended_edges_all_pairs_for_three_attendees(interaction_sync_pool):
    """Three attendees at the same event produce 3 pairs × 2 directions = 6 edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entities = []
        emails = ["alice@ex.com", "bob@ex.com", "carol@ex.com"]
        for email in emails:
            eid = await _make_entity(pool)
            await _link_email(pool, entity_id=eid, email=email)
            entities.append(eid)

        await _insert_calendar_event(
            pool,
            attendees=[{"email": e, "responseStatus": "accepted"} for e in emails]
            + [{"email": "me@owner.com", "responseStatus": "accepted", "self": True}],
        )

        result = await run_interaction_sync(pool)

        assert result["co_attended_edges_minted"] == 6
        assert result["errors"] == 0

        # Every ordered pair should exist.
        total_ef_rows = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE predicate = 'co-attended'"
        )
        assert total_ef_rows == 6


@pytest.mark.pg_clock
async def test_no_co_attended_edges_for_entities_in_different_events(interaction_sync_pool):
    """Entities attending different events do not receive co-attended edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entity_a = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_a, email="alice@example.com")

        entity_b = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_b, email="bob@example.com")

        # Each entity attends a SEPARATE event — no shared event.
        await _insert_calendar_event(
            pool,
            title="Alice's Meeting",
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )
        await _insert_calendar_event(
            pool,
            title="Bob's Meeting",
            attendees=[
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 2
        assert result["co_attended_edges_minted"] == 0
        assert result["errors"] == 0

        edges = await _co_attended_edges(pool, entity_a, entity_b)
        assert len(edges) == 0


@pytest.mark.pg_clock
async def test_no_co_attended_edges_for_sole_attendee(interaction_sync_pool):
    """A single resolved attendee per event produces no co-attended edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entity_a = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_a, email="alone@example.com")

        await _insert_calendar_event(
            pool,
            attendees=[
                {"email": "alone@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        result = await run_interaction_sync(pool)

        assert result["calendar_events_scanned"] == 1
        assert result["co_attended_edges_minted"] == 0
        assert result["errors"] == 0


@pytest.mark.pg_clock
async def test_co_attended_edges_idempotent_on_second_run(interaction_sync_pool):
    """Running interaction_sync twice for the same event does not duplicate edges."""
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        entity_a = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_a, email="alice@example.com")

        entity_b = await _make_entity(pool)
        await _link_email(pool, entity_id=entity_b, email="bob@example.com")

        await _insert_calendar_event(
            pool,
            attendees=[
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "accepted"},
                {"email": "me@owner.com", "responseStatus": "accepted", "self": True},
            ],
        )

        first = await run_interaction_sync(pool)
        assert first["co_attended_edges_minted"] == 2

        # Second run: edges already exist → outcome is 'unchanged', not re-minted.
        second = await run_interaction_sync(pool)
        assert second["co_attended_edges_minted"] == 0
        assert second["errors"] == 0

        # Still exactly 2 active rows in entity_facts — no duplicates.
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM relationship.entity_facts WHERE predicate = 'co-attended'"
        )
        assert count == 2
