"""The encounter ledger reducer on real migrated Postgres (bu-5ii4gj.3 E2a). Synthetic data only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.pg_clock

_OWNER = "owner@mine.example"
_ACCEPTED = {"email": _OWNER, "responseStatus": "accepted", "self": True}


@pytest.fixture(autouse=True)
async def _empty_ledger(interaction_sync_database):
    """The harness resets calendar sources per case, not these two relationship tables."""
    async with interaction_sync_database.control() as control:
        await control.execute("TRUNCATE relationship.encounters, relationship.meeting_debriefs")


def _reducer():
    from butlers.jobs import encounter_ledger

    return encounter_ledger


async def _person(pool, name: str, email: str) -> uuid.UUID:
    from roster.relationship.tests.calendar_projection import link_identity, make_entity

    entity_id = await make_entity(pool)
    await link_identity(pool, entity_id=entity_id, predicate="has-email", value=email)
    return entity_id


async def _owner(pool) -> None:
    from roster.relationship.tests.calendar_projection import link_identity, make_entity

    entity_id = await make_entity(pool, roles=["owner"])
    await link_identity(pool, entity_id=entity_id, predicate="has-email", value=_OWNER)


async def _event(pool, guests: list[dict], *, hours_ago: float = 3, status: str = "confirmed"):
    from butlers.modules.calendar.participants import sync_event_participants
    from roster.relationship.tests.calendar_projection import project_event

    starts = datetime.now(UTC) - timedelta(hours=hours_ago)
    event_id = await project_event(
        pool, starts_at=starts, attendees=[_ACCEPTED, *guests], status=status
    )
    await sync_event_participants(pool, event_id)
    return event_id


async def _rows(pool) -> dict[uuid.UUID, tuple[str, str]]:
    return {
        r["entity_id"]: (r["state"], r["basis"])
        for r in await pool.fetch("SELECT entity_id, state, basis FROM relationship.encounters")
    }


async def _debrief(pool, event_id: uuid.UUID, state: str) -> uuid.UUID:
    row = await pool.fetchrow(
        "SELECT starts_at, ends_at, title FROM calendar_events WHERE id = $1", event_id
    )
    return await pool.fetchval(
        """
        INSERT INTO meeting_debriefs
            (event_id, occurrence_start, occurrence_end, event_title, attendees, state)
        VALUES ($1, $2, $3, $4, '[]'::jsonb, $5) RETURNING id
        """,
        event_id,
        row["starts_at"],
        row["ends_at"],
        row["title"],
        state,
    )


async def test_an_ended_meeting_nobody_vouches_for_is_unknown(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        await _event(pool, [{"email": "alice@example.com", "responseStatus": "accepted"}])

        result = await _reducer().run_encounter_reducer(pool)

        assert result["complete"] is True and result["upserted"] == 1
        assert await _rows(pool) == {alice: ("unknown", "schedule")}


async def test_a_future_meeting_is_scheduled(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        await _event(
            pool,
            [{"email": "alice@example.com", "responseStatus": "accepted"}],
            hours_ago=-5,
        )

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {alice: ("scheduled", "schedule")}


async def test_a_guest_who_declined_is_declined_by_them(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        came = await _person(pool, "Came", "came@example.com")
        away = await _person(pool, "Away", "away@example.com")
        await _event(
            pool,
            [
                {"email": "came@example.com", "responseStatus": "accepted"},
                {"email": "away@example.com", "responseStatus": "declined"},
            ],
        )

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {
            came: ("unknown", "schedule"),
            away: ("declined_by_them", "rsvp"),
        }


async def test_the_owner_declining_is_declined_by_owner_for_every_guest(interaction_sync_pool):
    from butlers.modules.calendar.participants import sync_event_participants
    from roster.relationship.tests.calendar_projection import project_event

    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        event_id = await project_event(
            pool,
            starts_at=datetime.now(UTC) - timedelta(hours=3),
            attendees=[
                {"email": _OWNER, "responseStatus": "declined", "self": True},
                {"email": "alice@example.com", "responseStatus": "accepted"},
            ],
        )
        await sync_event_participants(pool, event_id)

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {alice: ("declined_by_owner", "rsvp")}


async def test_a_cancelled_meeting_is_cancelled(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        await _event(
            pool,
            [{"email": "alice@example.com", "responseStatus": "accepted"}],
            status="cancelled",
        )

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {alice: ("cancelled", "schedule")}


@pytest.mark.parametrize(
    ("debrief_state", "expected"),
    [
        ("none_agreed", "happened_attested"),
        ("captured", "happened_attested"),
        ("did_not_happen", "did_not_happen"),
        ("moved", "moved"),
    ],
)
async def test_the_owners_debrief_answer_decides(interaction_sync_pool, debrief_state, expected):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        debrief_id = await _debrief(pool, event_id, debrief_state)

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {alice: (expected, "owner_debrief")}
        refs = await pool.fetchval("SELECT evidence_refs FROM relationship.encounters")
        assert refs == {"debrief_id": str(debrief_id)}


async def test_pending_and_expired_debriefs_are_not_testimony(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        await _debrief(pool, event_id, "expired")

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {alice: ("unknown", "schedule")}


async def test_a_happened_debrief_does_not_overrule_a_guests_decline(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        came = await _person(pool, "Came", "came@example.com")
        away = await _person(pool, "Away", "away@example.com")
        event_id = await _event(
            pool,
            [
                {"email": "came@example.com", "responseStatus": "accepted"},
                {"email": "away@example.com", "responseStatus": "declined"},
            ],
        )
        await _debrief(pool, event_id, "captured")

        await _reducer().run_encounter_reducer(pool)

        assert await _rows(pool) == {
            came: ("happened_attested", "owner_debrief"),
            away: ("declined_by_them", "rsvp"),
        }


async def test_only_recordable_people_get_a_row(interaction_sync_pool):
    """Unresolved, resource, owner and non-active entities never become an encounter."""
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        quiet = await _person(pool, "Quiet", "quiet@example.com")
        await pool.execute("UPDATE public.entities SET posture = 'no_contact' WHERE id = $1", quiet)
        await _event(
            pool,
            [
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "quiet@example.com", "responseStatus": "accepted"},
                {"email": "stranger@example.com", "responseStatus": "accepted"},
                {"email": "room@resource.calendar.google.com", "responseStatus": "accepted"},
            ],
        )

        await _reducer().run_encounter_reducer(pool)

        assert set(await _rows(pool)) == {alice}


async def test_replay_converges_and_a_changed_input_moves_the_state(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        reducer = _reducer()

        await reducer.run_encounter_reducer(pool)
        first = await pool.fetch("SELECT * FROM relationship.encounters")
        await reducer.run_encounter_reducer(pool)
        again = await pool.fetch("SELECT * FROM relationship.encounters")
        assert [dict(r) for r in first] == [dict(r) for r in again]  # even updated_at is untouched

        await _debrief(pool, event_id, "none_agreed")
        await reducer.run_encounter_reducer(pool)
        assert await _rows(pool) == {alice: ("happened_attested", "owner_debrief")}

        await pool.execute("DELETE FROM meeting_debriefs")
        await reducer.run_encounter_reducer(pool)
        assert await _rows(pool) == {alice: ("unknown", "schedule")}


async def test_a_guest_removed_from_the_event_loses_the_row(interaction_sync_pool):
    from butlers.modules.calendar.participants import sync_event_participants

    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        bob = await _person(pool, "Bob", "bob@example.com")
        event_id = await _event(
            pool,
            [
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "accepted"},
            ],
        )
        await _reducer().run_encounter_reducer(pool)
        assert set(await _rows(pool)) == {alice, bob}

        await pool.execute(
            "UPDATE calendar_events SET metadata = jsonb_set(metadata, '{attendees}', $2::jsonb) "
            "WHERE id = $1",
            event_id,
            [_ACCEPTED, {"email": "alice@example.com", "responseStatus": "accepted"}],
        )
        await sync_event_participants(pool, event_id)
        await _reducer().run_encounter_reducer(pool)

        assert set(await _rows(pool)) == {alice}


async def test_an_event_whose_participants_are_not_ready_keeps_its_prior_rows(
    interaction_sync_pool,
):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        await _reducer().run_encounter_reducer(pool)
        before = await _rows(pool)

        # The stored participant rows no longer match the invitation: not ready, not "no one".
        await pool.execute("DELETE FROM calendar_event_participants WHERE event_id = $1", event_id)
        result = await _reducer().run_encounter_reducer(pool)

        assert result["complete"] is True and result["skipped"] == 1
        assert await _rows(pool) == before == {alice: ("unknown", "schedule")}


async def test_an_unreadable_input_leaves_prior_states_unchanged(
    interaction_sync_pool, monkeypatch
):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        reducer = _reducer()
        await reducer.run_encounter_reducer(pool)
        await _debrief(pool, event_id, "did_not_happen")  # the next run would change the state

        async def broken(executor, event_ids):
            raise RuntimeError("participants unreadable")

        monkeypatch.setattr(reducer, "unavailable_event_ids", broken)
        result = await reducer.run_encounter_reducer(pool)

        assert result["complete"] is False and result["upserted"] == 0
        assert await _rows(pool) == {alice: ("unknown", "schedule")}


async def test_a_failed_write_rolls_the_whole_run_back(
    interaction_sync_pool, interaction_sync_database
):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        await _person(pool, "Alice", "alice@example.com")
        await _person(pool, "Bob", "bob@example.com")
        await _event(
            pool,
            [
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "bob@example.com", "responseStatus": "accepted"},
            ],
        )
        async with interaction_sync_database.control() as control:
            await control.execute(
                "CREATE OR REPLACE FUNCTION relationship.reject_second() RETURNS trigger AS $$ "
                "BEGIN IF (SELECT count(*) FROM relationship.encounters) >= 1 THEN "
                "RAISE EXCEPTION 'boom'; END IF; RETURN NEW; END $$ LANGUAGE plpgsql"
            )
            await control.execute(
                "CREATE TRIGGER reject_second BEFORE INSERT ON relationship.encounters "
                "FOR EACH ROW EXECUTE FUNCTION relationship.reject_second()"
            )
            try:
                with pytest.raises(Exception, match="boom"):
                    await _reducer().run_encounter_reducer(pool)
                assert await _rows(pool) == {}
            finally:
                await control.execute("DROP TRIGGER reject_second ON relationship.encounters")


def test_reduce_state_precedence_is_total():
    reduce_state = _reducer().reduce_state

    def state(debrief=None, cancelled=False, owner=False, guest=False, started=True):
        return reduce_state(
            debrief_state=debrief,
            cancelled=cancelled,
            owner_declined=owner,
            guest_declined=guest,
            started=started,
        )

    assert state() == ("unknown", "schedule")
    assert state(started=False) == ("scheduled", "schedule")
    everything = dict(cancelled=True, owner=True, guest=True)
    assert state(**everything) == ("declined_by_them", "rsvp")
    assert state(debrief="none_agreed", **everything) == ("declined_by_them", "rsvp")
    assert state(debrief="moved", **everything) == ("moved", "owner_debrief")
    assert state(debrief="captured", cancelled=True, owner=True) == (
        "happened_attested",
        "owner_debrief",
    )


async def test_the_relationship_role_holds_the_ledger_grant(interaction_sync_pool):
    """The harness pool runs ``SET ROLE butler_relationship_rw``, so this proves the migration grant."""
    async with interaction_sync_pool() as pool:
        assert await pool.fetchval("SELECT current_user") == "butler_relationship_rw"
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await pool.fetchval(
                "SELECT has_table_privilege(current_user, 'relationship.encounters', $1)", privilege
            )


async def _occurrence_rows(pool) -> set:
    return {
        (r["event_id"], r["occurrence_start"])
        for r in await pool.fetch("SELECT event_id, occurrence_start FROM relationship.encounters")
    }


async def test_a_rescheduled_event_leaves_no_ghost_occurrence(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        await _reducer().run_encounter_reducer(pool)
        [(_, tuesday)] = await _occurrence_rows(pool)

        thursday = tuesday + timedelta(days=2)
        await pool.execute(
            "UPDATE calendar_events SET starts_at = $2, ends_at = $2::timestamptz + interval '1 hour' "
            "WHERE id = $1",
            event_id,
            thursday,
        )
        await _reducer().run_encounter_reducer(pool)

        assert await _occurrence_rows(pool) == {(event_id, thursday)}


async def test_a_deleted_event_leaves_no_ledger_rows(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        await _reducer().run_encounter_reducer(pool)
        assert await _occurrence_rows(pool)

        await pool.execute("DELETE FROM calendar_events WHERE id = $1", event_id)
        result = await _reducer().run_encounter_reducer(pool)

        assert result["complete"] is True and result["removed"] == 1
        assert await _occurrence_rows(pool) == set()


async def test_a_dropped_recurring_instance_leaves_no_ledger_row(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        await _person(pool, "Alice", "alice@example.com")
        event_id = await _event(
            pool, [{"email": "alice@example.com", "responseStatus": "accepted"}]
        )
        starts = await pool.fetchval(
            "SELECT starts_at FROM calendar_events WHERE id = $1", event_id
        )
        await pool.execute(
            "UPDATE calendar_events SET recurrence_rule = 'FREQ=DAILY' WHERE id = $1", event_id
        )
        await pool.execute("DELETE FROM calendar_event_instances WHERE event_id = $1", event_id)
        later = starts + timedelta(days=1)
        for at in (starts, later):
            await pool.execute(
                "INSERT INTO calendar_event_instances (event_id, source_id, origin_instance_ref,"
                " starts_at, ends_at, timezone, status, metadata) "
                "SELECT id, source_id, 'inst-' || $2::timestamptz::text, $2::timestamptz, $2::timestamptz + interval '1 hour', 'UTC', 'confirmed', '{}'::jsonb"
                " FROM calendar_events WHERE id = $1",
                event_id,
                at,
            )
        await _reducer().run_encounter_reducer(pool)
        assert await _occurrence_rows(pool) == {(event_id, starts), (event_id, later)}

        await pool.execute(
            "DELETE FROM calendar_event_instances WHERE event_id = $1 AND starts_at = $2",
            event_id,
            later,
        )
        await _reducer().run_encounter_reducer(pool)

        assert await _occurrence_rows(pool) == {(event_id, starts)}


async def test_the_read_cap_never_purges_valid_history(interaction_sync_pool, monkeypatch):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        await _person(pool, "Alice", "alice@example.com")
        guest = [{"email": "alice@example.com", "responseStatus": "accepted"}]
        near = await _event(pool, guest, hours_ago=2)
        far = await _event(pool, guest, hours_ago=24 * 30)
        reducer = _reducer()
        await reducer.run_encounter_reducer(pool)
        assert {e for e, _ in await _occurrence_rows(pool)} == {near, far}

        monkeypatch.setattr(reducer, "MAX_OCCURRENCES", 1)
        await reducer.run_encounter_reducer(pool)

        assert {e for e, _ in await _occurrence_rows(pool)} == {near, far}


async def test_the_capped_read_prefers_the_nearest_occurrences(interaction_sync_pool, monkeypatch):
    async with interaction_sync_pool() as pool:
        await _owner(pool)
        await _person(pool, "Alice", "alice@example.com")
        guest = [{"email": "alice@example.com", "responseStatus": "accepted"}]
        await _event(pool, guest, hours_ago=24 * 60)
        near = await _event(pool, guest, hours_ago=3)
        reducer = _reducer()
        monkeypatch.setattr(reducer, "MAX_OCCURRENCES", 1)

        await reducer.run_encounter_reducer(pool)

        assert {e for e, _ in await _occurrence_rows(pool)} == {near}


async def test_an_entity_merge_repoints_the_ledger(interaction_sync_pool):
    from butlers.entity_rebind import _repoint_encounters

    async with interaction_sync_pool() as pool:
        await _owner(pool)
        alice = await _person(pool, "Alice", "alice@example.com")
        real = await _person(pool, "Alice Real", "alice.real@example.com")
        event_id = await _event(
            pool,
            [
                {"email": "alice@example.com", "responseStatus": "accepted"},
                {"email": "alice.real@example.com", "responseStatus": "accepted"},
            ],
        )
        other = await _event(pool, [{"email": "alice@example.com", "responseStatus": "accepted"}])
        await _reducer().run_encounter_reducer(pool)

        async with pool.acquire() as conn:
            changed = await _repoint_encounters(conn, alice, real)

        rows = await pool.fetch("SELECT entity_id, event_id FROM relationship.encounters")
        assert alice not in {r["entity_id"] for r in rows}
        # Both survivors' rows exist once: the collision on the shared event dropped the source's.
        assert sorted((str(r["entity_id"]), str(r["event_id"])) for r in rows) == sorted(
            [(str(real), str(event_id)), (str(real), str(other))]
        )
        assert changed == 2
