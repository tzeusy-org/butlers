"""Real-Postgres meeting debrief (bu-q7vx1q.12): selection, prompt, answer, prep rail.

Runs the core, memory and relationship Alembic chains, so ``relationship.meeting_debriefs``,
``public.owner_conditions`` and the calendar projection are the production shapes. The
insight broker is the only stand-in: it is a recording proposer, because its queue lives in
the Switchboard schema and delivery is not what is under test.

People are synthetic. Calendar rows are seeded straight into ``calendar_events`` (the
projection the production calendar sync writes), so the job's selection SQL runs unmodified.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.calendar_prep import prep_key, run_relationship_calendar_prep_contribution
from butlers.jobs.meeting_debrief import (
    BACKOFF_CADENCE,
    DEBRIEF_CATEGORY,
    run_meeting_debrief,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.meeting_debrief import (
    meeting_debrief_answer,
    meeting_debrief_pending,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_TRUNCATE = [
    "public.owner_conditions",
    "public.state",
    "public.entities",
    "calendar_event_entities",
    "calendar_events",
    "relationship.meeting_debriefs",
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """A pool resolving bare table names the way a butler pool does (own schema first)."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "relationship, public"},
    )
    for table in _TRUNCATE:
        await p.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    yield p
    await p.close()


class _Proposer:
    """Records insight proposals and answers with a scripted status."""

    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses) or ["accepted"]
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, pool: asyncpg.Pool, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"status": status, "reason": "scripted"}


async def _person(pool, name: str, email: str, *, posture: str = "active", owner: bool = False):
    entity_id = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ($1, 'person', $2::text[]) RETURNING id
        """,
        name,
        ["owner"] if owner else [],
    )
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src)
        VALUES ($1, 'has-email', $2, 'literal', 'test')
        """,
        entity_id,
        email,
    )
    if posture != "active":
        await pool.execute(
            "UPDATE public.entities SET posture = $2 WHERE id = $1", entity_id, posture
        )
    return entity_id


async def _source(pool) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO calendar_sources (source_key, source_kind)
        VALUES ($1, 'provider_event') RETURNING id
        """,
        f"test-{uuid.uuid4().hex[:8]}",
    )


def _attendees(*others: str, owner_status: str = "accepted", with_self: bool = True):
    people = [{"email": e, "response_status": "accepted"} for e in others]
    if with_self:
        people.insert(
            0, {"email": "owner@example.test", "self": True, "response_status": owner_status}
        )
    return people


async def _event(
    pool,
    source_id,
    title: str,
    *,
    ended_ago: timedelta = timedelta(hours=2),
    attendees: list[dict] | None = None,
    extra_meta: dict | None = None,
    all_day: bool = False,
    status: str = "confirmed",
    recurrence_rule: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> uuid.UUID:
    ends = ends_at or (datetime.now(UTC) - ended_ago)
    starts = starts_at or (ends - timedelta(hours=1))
    meta = {"attendees": attendees or []}
    meta.update(extra_meta or {})
    return await pool.fetchval(
        """
        INSERT INTO calendar_events
            (source_id, origin_ref, title, timezone, starts_at, ends_at, all_day, status,
             recurrence_rule, metadata, source_butler)
        VALUES ($1, $2, $3, 'UTC', $4, $5, $6, $7, $8, $9::text::jsonb, 'relationship')
        RETURNING id
        """,
        source_id,
        uuid.uuid4().hex,
        title,
        starts,
        ends,
        all_day,
        status,
        recurrence_rule,
        json.dumps(meta),
    )


async def _titles(pool) -> set[str]:
    return {r["event_title"] for r in await pool.fetch("SELECT event_title FROM meeting_debriefs")}


async def _commitments(pool) -> list[dict]:
    rows = await pool.fetch(
        "SELECT summary, metadata FROM public.owner_conditions "
        "WHERE metadata->>'class' = 'commitment' ORDER BY first_detected_at"
    )
    return [
        {
            "summary": r["summary"],
            "metadata": json.loads(r["metadata"])
            if isinstance(r["metadata"], str)
            else r["metadata"],
        }
        for r in rows
    ]


class TestSelection:
    async def test_excluded_meetings_get_no_row_and_two_runs_make_one_row(self, pool) -> None:
        """A declined meeting, a solo block, an owner-only meeting and others are excluded."""
        src = await _source(pool)
        await _person(pool, "Owner Person", "owner@example.test", owner=True)
        await _person(pool, "Sam Rivera", "sam@example.test")
        await _person(pool, "Maya Quiet", "maya@example.test", posture="memorial")

        await _event(pool, src, "With Sam", attendees=_attendees("sam@example.test"))
        await _event(
            pool, src, "Declined", attendees=_attendees("sam@example.test", owner_status="declined")
        )
        await _event(pool, src, "Solo block", attendees=_attendees())
        await _event(pool, src, "Owner only", attendees=_attendees("owner@example.test"))
        await _event(
            pool,
            src,
            "Free time",
            attendees=_attendees("sam@example.test"),
            extra_meta={"transparency": "transparent"},
        )
        await _event(pool, src, "All day", attendees=_attendees("sam@example.test"), all_day=True)
        await _event(
            pool, src, "Cancelled", attendees=_attendees("sam@example.test"), status="cancelled"
        )
        await _event(
            pool,
            src,
            "Not yet",
            attendees=_attendees("sam@example.test"),
            ended_ago=timedelta(hours=-3),
        )
        await _event(
            pool,
            src,
            "Butler made",
            attendees=_attendees("sam@example.test"),
            extra_meta={"butler_generated": True},
        )
        await _event(pool, src, "Memorial only", attendees=_attendees("maya@example.test"))
        await _event(pool, src, "Stranger", attendees=_attendees("new.person@example.test"))

        proposer = _Proposer()
        first = await run_meeting_debrief(pool, insight_proposer=proposer)
        second = await run_meeting_debrief(pool, insight_proposer=proposer)

        assert await _titles(pool) == {"With Sam", "Stranger"}
        assert first["recorded"] == 2 and second["recorded"] == 0
        assert len(proposer.calls) == 1, "a meeting is prompted at most once"
        message = proposer.calls[0]["message"]
        assert "With Sam (Sam Rivera)" in message
        assert "new.person@example.test" in message
        assert "Maya" not in message and "Memorial only" not in message
        assert proposer.calls[0]["category"] == DEBRIEF_CATEGORY

    async def test_recurring_series_is_debriefed_per_occurrence(self, pool) -> None:
        src = await _source(pool)
        await _person(pool, "Sam Rivera", "sam@example.test")
        now = datetime.now(UTC)
        event_id = await _event(
            pool,
            src,
            "Weekly 1:1",
            attendees=_attendees("sam@example.test"),
            recurrence_rule="FREQ=DAILY",
            starts_at=now - timedelta(days=3),
            ends_at=now - timedelta(days=3) + timedelta(hours=1),
        )
        for days in (1, 0):
            start = now - timedelta(days=days, hours=3)
            await pool.execute(
                """
                INSERT INTO calendar_event_instances
                    (event_id, source_id, origin_instance_ref, timezone, starts_at, ends_at)
                VALUES ($1, $2, $3, 'UTC', $4, $5)
                """,
                event_id,
                src,
                f"occ-{days}",
                start,
                start + timedelta(hours=1),
            )

        await run_meeting_debrief(pool, insight_proposer=_Proposer())

        rows = await pool.fetch("SELECT occurrence_start FROM meeting_debriefs")
        assert len(rows) == 2

    async def test_a_failed_proposal_is_retried_not_marked_prompted(self, pool) -> None:
        src = await _source(pool)
        await _person(pool, "Sam Rivera", "sam@example.test")
        await _event(pool, src, "With Sam", attendees=_attendees("sam@example.test"))

        proposer = _Proposer("error", "accepted")
        await run_meeting_debrief(pool, insight_proposer=proposer)
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM meeting_debriefs WHERE prompted_at IS NOT NULL"
            )
            == 0
        )
        await run_meeting_debrief(pool, insight_proposer=proposer)

        assert len(proposer.calls) == 2
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM meeting_debriefs WHERE prompted_at IS NOT NULL"
            )
            == 1
        )

    async def test_person_marked_no_contact_after_selection_is_not_asked_about(self, pool) -> None:
        src = await _source(pool)
        sam = await _person(pool, "Sam Rivera", "sam@example.test")
        await _event(pool, src, "With Sam", attendees=_attendees("sam@example.test"))

        proposer = _Proposer("filtered")
        await run_meeting_debrief(pool, insight_proposer=proposer)  # recorded, not accepted
        await pool.execute("UPDATE public.entities SET posture = 'no_contact' WHERE id = $1", sam)
        proposer.statuses = ["accepted"]
        result = await run_meeting_debrief(pool, insight_proposer=proposer)

        assert result["prompted"] == 0
        assert len(proposer.calls) == 1, "the second run found nothing askable"
        assert await pool.fetchval("SELECT state FROM meeting_debriefs") == "expired"


async def _prompted_batch(pool, *, ago: timedelta, answered: bool = False) -> None:
    prompted_at = datetime.now(UTC) - ago
    await pool.execute(
        """
        INSERT INTO meeting_debriefs
            (event_id, occurrence_start, occurrence_end, event_title, attendees, state,
             prompted_at, answered_at)
        VALUES ($1, $2, $3, 'Old meeting', '[]'::jsonb, $4, $5, $6)
        """,
        uuid.uuid4(),
        prompted_at - timedelta(hours=3),
        prompted_at - timedelta(hours=2),
        "captured" if answered else "pending",
        prompted_at,
        prompted_at + timedelta(minutes=5) if answered else None,
    )


class TestBackoff:
    async def _fresh_meeting(self, pool) -> None:
        src = await _source(pool)
        await _person(pool, "Sam Rivera", "sam@example.test")
        await _event(pool, src, "With Sam", attendees=_attendees("sam@example.test"))

    async def test_three_unanswered_batches_drop_the_cadence_to_weekly(self, pool) -> None:
        for days in (3, 2, 1):
            await _prompted_batch(pool, ago=timedelta(days=days))
        await self._fresh_meeting(pool)
        proposer = _Proposer()

        result = await run_meeting_debrief(pool, insight_proposer=proposer)

        assert result["skipped"] == "backoff" and proposer.calls == []

    async def test_weekly_batch_goes_out_once_the_cadence_has_elapsed(self, pool) -> None:
        for days in (12, 11, 10):
            await _prompted_batch(pool, ago=timedelta(days=days))
        assert timedelta(days=10) > BACKOFF_CADENCE
        await self._fresh_meeting(pool)
        proposer = _Proposer()

        await run_meeting_debrief(pool, insight_proposer=proposer)

        assert len(proposer.calls) == 1

    async def test_the_batch_that_crosses_the_threshold_says_so(self, pool) -> None:
        for days in (3, 2):
            await _prompted_batch(pool, ago=timedelta(days=days))
        await self._fresh_meeting(pool)
        proposer = _Proposer()

        await run_meeting_debrief(pool, insight_proposer=proposer)

        assert "ask weekly" in proposer.calls[0]["message"]

    async def test_an_answer_restores_the_daily_cadence(self, pool) -> None:
        for days in (4, 3, 2):
            await _prompted_batch(pool, ago=timedelta(days=days))
        await _prompted_batch(pool, ago=timedelta(hours=30), answered=True)
        await self._fresh_meeting(pool)
        proposer = _Proposer()

        await run_meeting_debrief(pool, insight_proposer=proposer)

        assert len(proposer.calls) == 1


class TestAnswer:
    async def _debrief(
        self, pool, *attendee_emails: str, known: dict[str, uuid.UUID] | None = None
    ):
        src = await _source(pool)
        event_id = await _event(pool, src, "Roadmap sync", attendees=_attendees(*attendee_emails))
        await run_meeting_debrief(pool, insight_proposer=_Proposer())
        debrief_id = await pool.fetchval("SELECT id FROM meeting_debriefs")
        return event_id, str(debrief_id)

    async def test_answer_creates_a_commitment_whose_evidence_names_the_meeting(self, pool) -> None:
        sam = await _person(pool, "Sam Rivera", "sam@example.test")
        event_id, debrief_id = await self._debrief(pool, "sam@example.test")

        pending = await meeting_debrief_pending(pool)
        assert [d["debrief_id"] for d in pending["debriefs"]] == [debrief_id]
        assert pending["debriefs"][0]["number"] == 1

        result = await meeting_debrief_answer(
            pool,
            debrief_id=debrief_id,
            commitments=[
                {
                    "summary": "Send Sam the deck",
                    "deadline": "2031-03-07T17:00:00+00:00",
                    "sphere": "work",
                }
            ],
            session_id="session-1",
        )

        assert result["status"] == "captured"
        (row,) = await _commitments(pool)
        meta = row["metadata"]
        assert meta["evidence_opened"]["source"] == "meeting_debrief"
        assert meta["evidence_opened"]["event_id"] == str(event_id)
        assert meta["counterparty_entity_id"] == str(sam), "the sole attendee is the default"
        assert meta["sphere"] == "work"
        assert meta["deadline"].startswith("2031-03-07")
        state = await pool.fetchrow("SELECT state, answered_at FROM meeting_debriefs")
        assert state["state"] == "captured" and state["answered_at"] is not None
        assert (await meeting_debrief_pending(pool))["debriefs"] == []

    async def test_next_prep_envelope_for_the_counterparty_carries_the_commitment(
        self, pool
    ) -> None:
        sam = await _person(pool, "Sam Rivera", "sam@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")
        await meeting_debrief_answer(
            pool, debrief_id=debrief_id, commitments=[{"summary": "Send Sam the deck"}]
        )
        src = await _source(pool)
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        next_event = await _event(
            pool,
            src,
            "Follow-up with Sam",
            attendees=_attendees("sam@example.test"),
            starts_at=tomorrow,
            ends_at=tomorrow + timedelta(hours=1),
        )
        await pool.execute(
            "INSERT INTO calendar_event_entities (event_id, entity_id) VALUES ($1, $2)",
            next_event,
            sam,
        )

        await run_relationship_calendar_prep_contribution(pool, None)

        raw = await pool.fetchval(
            "SELECT value FROM public.state WHERE key = $1", prep_key(str(next_event))
        )
        envelope = json.loads(raw) if isinstance(raw, str) else raw
        (attendee,) = envelope["attendees"]
        assert [c["summary"] for c in attendee["commitments"]] == ["Send Sam the deck"]

    async def test_none_is_a_recorded_answer_and_a_second_answer_changes_nothing(
        self, pool
    ) -> None:
        await _person(pool, "Sam Rivera", "sam@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")

        first = await meeting_debrief_answer(pool, debrief_id=debrief_id)
        second = await meeting_debrief_answer(
            pool, debrief_id=debrief_id, commitments=[{"summary": "Send Sam the deck"}]
        )

        assert first["status"] == "none_agreed"
        assert second["status"] == "already_answered"
        assert await _commitments(pool) == []
        assert await pool.fetchval("SELECT state FROM meeting_debriefs") == "none_agreed"

    async def test_counterparty_outside_the_meeting_is_refused(self, pool) -> None:
        await _person(pool, "Sam Rivera", "sam@example.test")
        outsider = await _person(pool, "Priya Outsider", "priya@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")

        result = await meeting_debrief_answer(
            pool,
            debrief_id=debrief_id,
            commitments=[
                {"summary": "Send Sam the deck"},
                {"summary": "Call Priya", "counterparty_entity_id": str(outsider)},
            ],
        )

        assert result["status"] == "invalid"
        assert await _commitments(pool) == [], "validated before the first write"
        assert await pool.fetchval("SELECT state FROM meeting_debriefs") == "pending"

    async def test_unresolved_attendee_yields_a_null_counterparty_commitment(self, pool) -> None:
        _event_id, debrief_id = await self._debrief(pool, "new.person@example.test")

        result = await meeting_debrief_answer(
            pool, debrief_id=debrief_id, commitments=[{"summary": "Send the deck"}]
        )

        assert result["status"] == "captured"
        (row,) = await _commitments(pool)
        assert row["metadata"]["counterparty_entity_id"] is None
