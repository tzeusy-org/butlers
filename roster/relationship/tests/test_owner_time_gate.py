"""Only the owner's own time becomes a Chronicle episode, an interaction or an encounter.

bu-5ii4gj.16: an event on a partner or shared calendar can list the owner's address as an accepted
attendee without being the owner's time. The chronicler calendar adapter, interaction sync and the
encounter ledger all read it through ``counts_as_owner_time``. Real Postgres; synthetic data only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.roster_imports import roster_module

pytestmark = pytest.mark.pg_clock

_OWNER = "owner@mine.example"
# The owner's address as an accepted attendee, with no ``self`` flag: it resolves as the owner's
# own participant row, yet only the calendar it sits on says whose time it is.
_LISTED = {"email": _OWNER, "responseStatus": "accepted"}
_GUEST = {"email": "alice@example.com", "responseStatus": "accepted"}


async def _world(pool):
    from roster.relationship.tests.calendar_projection import (
        link_identity,
        make_entity,
        project_event,
    )

    owner = await make_entity(pool, roles=["owner"])
    await link_identity(pool, entity_id=owner, predicate="has-email", value=_OWNER)
    alice = await make_entity(pool)
    await link_identity(pool, entity_id=alice, predicate="has-email", value="alice@example.com")
    ids = {}
    for title, role, calendar in (
        ("Partner event", "other_person", "partner-cal"),
        ("Primary event", "owner", "primary-cal"),
    ):
        event_id = await project_event(
            pool,
            title=title,
            starts_at=datetime.now(UTC) - timedelta(hours=3),
            attendees=[_LISTED, _GUEST],
            calendar_id=calendar,
            time_role=role,
        )
        from butlers.modules.calendar.participants import sync_event_participants

        await sync_event_participants(pool, event_id)
        ids[title] = event_id
    return alice, ids


async def test_the_ledger_records_a_primary_event_and_not_a_partner_one(interaction_sync_pool):
    from butlers.jobs.encounter_ledger import run_encounter_reducer

    async with interaction_sync_pool() as pool:
        alice, ids = await _world(pool)

        result = await run_encounter_reducer(pool)

        assert result["complete"] is True
        rows = await pool.fetch("SELECT event_id FROM relationship.encounters")
        assert {r["event_id"] for r in rows} == {ids["Primary event"]}


async def test_a_partner_event_already_in_the_ledger_is_pruned(interaction_sync_pool):
    from butlers.jobs.encounter_ledger import run_encounter_reducer

    async with interaction_sync_pool() as pool:
        alice, ids = await _world(pool)
        await pool.execute("UPDATE calendar_sources SET time_role = 'owner'")
        await run_encounter_reducer(pool)
        assert await pool.fetchval("SELECT count(*) FROM relationship.encounters") == 2

        await pool.execute(
            "UPDATE calendar_sources SET time_role = 'other_person' WHERE calendar_id = 'partner-cal'"
        )
        await run_encounter_reducer(pool)

        rows = await pool.fetch("SELECT event_id FROM relationship.encounters")
        assert {r["event_id"] for r in rows} == {ids["Primary event"]}


async def test_interaction_sync_logs_a_primary_event_and_not_a_partner_one(interaction_sync_pool):
    run_interaction_sync = roster_module(
        "relationship", "jobs/relationship_jobs"
    ).run_interaction_sync

    async with interaction_sync_pool() as pool:
        alice, ids = await _world(pool)

        result = await run_interaction_sync(pool)

        assert result["errors"] == 0
        # One scanned event, one interaction: the partner calendar's event is neither.
        assert result["calendar_events_scanned"] == 1
        assert result["logged"] == 1


async def test_the_chronicler_adapter_projects_a_primary_event_and_not_a_partner_one(
    interaction_sync_pool,
):
    from unittest.mock import patch

    from butlers.chronicler.adapters.calendar import CalendarCompletedAdapter
    from butlers.testing.fake_pool import AsyncCtx

    async with interaction_sync_pool() as pool:
        await _world(pool)
        episodes = []

        async def keep(_conn, episode):
            episodes.append(episode)
            return episode

        conn = AsyncMock()
        conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
        conn.fetchrow = AsyncMock(return_value=None)
        chronicler = AsyncMock()
        chronicler.acquire = MagicMock(side_effect=lambda: AsyncCtx(conn))
        adapter = CalendarCompletedAdapter(butler_schemas=("relationship",))
        with patch("butlers.chronicler.adapters.calendar.upsert_episode", side_effect=keep):
            result = await adapter.project(pool, chronicler_pool=chronicler, since=None)

        assert [e.title for e in episodes] == ["Primary event"]
        assert result.rows_projected == 1
