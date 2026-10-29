"""Real-Postgres meeting debrief (bu-q7vx1q.12): selection, prompt, answer, prep rail.

Runs the core, memory and relationship Alembic chains, so ``relationship.meeting_debriefs``,
``public.owner_conditions`` and the calendar projection are the production shapes. The
insight broker is the only stand-in: it is a recording proposer, because its queue lives in
the Switchboard schema and delivery is not what is under test.

People are synthetic. Calendar rows are seeded straight into ``calendar_events`` (the
projection the production calendar sync writes), so the job's selection SQL runs unmodified.

The bu-q7vx1q.68 batch/race controls use existing production symbols. Their first
publication is a test-only baseline; hosted causal failures are required before repair.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import uuid
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.core import commitments as commitment_tools
from butlers.core.insight_premise import owner_condition_premise
from butlers.db import register_jsonb_codec
from butlers.jobs import meeting_debrief as debrief_job
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
    "public.insight_candidates",
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
        max_size=1,
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


async def _effects(pool) -> dict[str, list[dict]]:
    """Read durable effects after the answer has released its owning acquisition."""
    async with pool.acquire() as conn:
        return {
            table: [dict(row) for row in await conn.fetch(f"SELECT * FROM {table} ORDER BY id")]
            for table in (
                "public.owner_conditions",
                "public.entity_graph_edges",
                "public.insight_candidates",
                "public.insight_amendments",
            )
        }


async def _sentinels(pool, counterparty) -> dict[str, list[dict]]:
    """Plant real condition/graph and unrelated premise receipts before an absence check.

    Creation has no resolved transition. These planted premise rows prove preservation,
    not amendment production, provider delivery or runtime-role admission.
    """
    transition = await commitment_tools.create_commitment(
        pool,
        source="relationship:sentinel",
        summary="Unrelated agreement survives",
        kind="promise",
        direction="owner_to_other",
        counterparty_entity_id=str(counterparty),
        confidence=0.9,
        evidence_opened={"source": "synthetic_sentinel"},
        action_description="Unrelated agreement survives",
    )
    assert transition is not None
    candidate_id = await pool.fetchval(
        """
        INSERT INTO public.insight_candidates
            (origin_butler, priority, category, dedup_key, expires_at, message,
             status, delivered_at, premise)
        VALUES ('relationship', 50, 'sentinel', 'debrief:sentinel', now() + interval '1 day',
                'Synthetic unrelated receipt', 'delivered', now(), $1::jsonb)
        RETURNING id
        """,
        owner_condition_premise(transition.source, transition.fingerprint),
    )
    await pool.execute(
        """
        INSERT INTO public.insight_amendments (candidate_id, episode_key, reason, summary)
        VALUES ($1, 'unrelated-episode', 'synthetic-sentinel', 'Existing amendment survives')
        """,
        candidate_id,
    )
    before = await _effects(pool)
    assert all(before.values()), "absence controls must contain positive planted rows"
    return before


async def _debrief_state(pool, debrief_id) -> dict:
    return dict(
        await pool.fetchrow(
            "SELECT state, answered_at, session_id FROM meeting_debriefs WHERE id = $1",
            uuid.UUID(debrief_id),
        )
    )


async def _wait_until_held(task, reached) -> None:
    """Surface an early owning-task error instead of misreporting a barrier timeout."""
    waiter = asyncio.create_task(reached.wait())
    try:
        done, _pending = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            pytest.fail("owning task completed before the actual fault/selection gate")
        assert reached.is_set() and not task.done()
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def _race_answers(migrated_db_url, debrief_id, answers) -> list[dict]:
    """Gate both actual handlers with a real debrief-row lock, including indirect waiters.

    The old handler waits at its final UPDATE; a row-locked handler waits at its
    initial read. The same gate works on both sources without a future helper.
    """
    tasks = []
    async with AsyncExitStack() as stack:
        guard = await asyncpg.connect(migrated_db_url)
        stack.push_async_callback(guard.close)
        observer = await asyncpg.connect(migrated_db_url)
        stack.push_async_callback(observer.close)
        contenders = []
        pids = []
        for index in range(2):
            contender = await stack.enter_async_context(
                asyncpg.create_pool(
                    migrated_db_url,
                    min_size=1,
                    max_size=1,
                    init=register_jsonb_codec,
                    server_settings={
                        "search_path": "relationship, public",
                        "application_name": f"debrief-contender-{index}",
                    },
                )
            )
            async with contender.acquire() as conn:
                pids.append(conn.get_server_pid())
            contenders.append(contender)
        try:
            async with guard.transaction():
                await guard.fetchrow(
                    "SELECT id FROM relationship.meeting_debriefs WHERE id = $1 FOR UPDATE",
                    uuid.UUID(debrief_id),
                )
                tasks = [
                    asyncio.create_task(
                        meeting_debrief_answer(contender, debrief_id=debrief_id, **answer)
                    )
                    for contender, answer in zip(contenders, answers, strict=True)
                ]
                async with asyncio.timeout(15):
                    while True:
                        for task in tasks:
                            if task.done():
                                task.result()
                        assert all(not task.done() for task in tasks), "contender left before gate"
                        blocked = await observer.fetch(
                            """
                            WITH RECURSIVE wait_chain(start_pid, pid, path) AS (
                                SELECT p.pid, p.pid, ARRAY[p.pid]
                                FROM unnest($1::integer[]) AS p(pid)
                                UNION ALL
                                SELECT w.start_pid, b.pid, w.path || b.pid
                                FROM wait_chain w
                                CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS b(pid)
                                WHERE NOT b.pid = ANY(w.path)
                            )
                            SELECT DISTINCT start_pid FROM wait_chain WHERE pid = $2
                            """,
                            pids,
                            guard.get_server_pid(),
                        )
                        if {row["start_pid"] for row in blocked} == set(pids):
                            break
            async with asyncio.timeout(15):
                return await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def _assert_winning_effects(pool, debrief_id, answers, results, before) -> None:
    statuses = sorted(result["status"] for result in results)
    assert statuses in (["already_answered", "captured"], ["already_answered", "none_agreed"])
    winner = next(
        index for index, result in enumerate(results) if result["status"] != "already_answered"
    )
    winning = answers[winner]
    expected_state = "captured" if winning["commitments"] else "none_agreed"
    assert results[winner]["status"] == expected_state
    expected = {item["summary"] for item in winning["commitments"]}
    after = await _effects(pool)
    new_conditions = [
        row
        for row in after["public.owner_conditions"]
        if row["id"] not in {old["id"] for old in before["public.owner_conditions"]}
    ]
    assert {row["summary"] for row in new_conditions} == expected
    debrief = await pool.fetchrow(
        "SELECT event_id, occurrence_start FROM meeting_debriefs WHERE id = $1",
        uuid.UUID(debrief_id),
    )
    for row in new_conditions:
        meta = row["metadata"]
        assert row["source"] == "relationship:commitment"
        assert meta["confidence"] == 0.9
        assert meta["kind"] == "promise" and meta["direction"] == "owner_to_other"
        evidence = meta["evidence_opened"]
        assert evidence == {
            "source": "meeting_debrief",
            "event_id": str(debrief["event_id"]),
            "occurrence_start": debrief["occurrence_start"].isoformat(),
            "debrief_id": debrief_id,
            "event_title": "Roadmap sync",
            "session_id": winning["session_id"],
        }
    graph = after["public.entity_graph_edges"]
    assert {row["source_id"] for row in graph} == {
        row["id"] for row in after["public.owner_conditions"]
    }
    owner = await pool.fetchval("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    assert all(
        edge["source_schema"] == "public"
        and edge["source_table"] == "owner_conditions"
        and edge["subject_entity_id"] == owner
        and edge["predicate"] == "committed-to"
        for edge in graph
    )
    for row in new_conditions:
        edge = next(edge for edge in graph if edge["source_id"] == row["id"])
        assert str(edge["object_entity_id"]) == row["metadata"]["counterparty_entity_id"]
    for table, old_rows in before.items():
        assert [
            row for row in after[table] if row["id"] in {old["id"] for old in old_rows}
        ] == old_rows
    state = await _debrief_state(pool, debrief_id)
    assert state["state"] == expected_state and state["answered_at"] is not None
    assert state["session_id"] == winning["session_id"]
    retry = await meeting_debrief_answer(pool, debrief_id=debrief_id, **answers[1 - winner])
    assert retry["status"] == "already_answered"
    assert await _effects(pool) == after
    assert await _debrief_state(pool, debrief_id) == state


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
        debrief_id = await pool.fetchval(
            "SELECT id FROM meeting_debriefs WHERE event_id = $1", event_id
        )
        return event_id, str(debrief_id)

    async def test_answer_creates_a_commitment_whose_evidence_names_the_meeting(self, pool) -> None:
        owner = await _person(pool, "Owner Person", "owner@example.test", owner=True)
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
                },
                {"summary": "Confirm the roadmap date"},
            ],
            session_id="session-1",
        )

        assert result["status"] == "captured"
        rows = await _commitments(pool)
        assert {row["summary"] for row in rows} == {
            "Send Sam the deck",
            "Confirm the roadmap date",
        }
        row = next(row for row in rows if row["summary"] == "Send Sam the deck")
        meta = row["metadata"]
        assert meta["evidence_opened"]["source"] == "meeting_debrief"
        assert meta["evidence_opened"]["event_id"] == str(event_id)
        assert meta["counterparty_entity_id"] == str(sam), "the sole attendee is the default"
        assert meta["sphere"] == "work"
        assert meta["deadline"].startswith("2031-03-07")
        effects = await _effects(pool)
        assert len(effects["public.owner_conditions"]) == 2
        assert {edge["source_id"] for edge in effects["public.entity_graph_edges"]} == {
            condition["id"] for condition in effects["public.owner_conditions"]
        }
        assert all(
            edge["subject_entity_id"] == owner
            and edge["object_entity_id"] == sam
            and edge["predicate"] == "committed-to"
            for edge in effects["public.entity_graph_edges"]
        )
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

    async def test_competing_distinct_answers_commit_only_the_winner(
        self, pool, migrated_db_url
    ) -> None:
        await _person(pool, "Owner Person", "owner@example.test", owner=True)
        sam = await _person(pool, "Sam Rivera", "sam@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")
        before = await _sentinels(pool, sam)
        answers = [
            {
                "commitments": [{"summary": "Send the alpha deck"}, {"summary": "Book alpha room"}],
                "session_id": "answer-alpha",
            },
            {
                "commitments": [{"summary": "Send the beta memo"}, {"summary": "Book beta lunch"}],
                "session_id": "answer-beta",
            },
        ]

        results = await _race_answers(migrated_db_url, debrief_id, answers)

        await _assert_winning_effects(pool, debrief_id, answers, results, before)

    @pytest.mark.parametrize(
        "mode", ["sequential-none", "competing-none", "stale-none", "stale-captured-self"]
    )
    async def test_none_is_a_recorded_answer_and_a_second_answer_changes_nothing(
        self, pool, migrated_db_url, monkeypatch, mode
    ) -> None:
        await _person(pool, "Owner Person", "owner@example.test", owner=True)
        sam = await _person(pool, "Sam Rivera", "sam@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")

        if mode == "competing-none":
            before = await _sentinels(pool, sam)
            answers = [
                {"commitments": [], "session_id": "answer-none"},
                {"commitments": [{"summary": "Send Sam the deck"}], "session_id": "answer-deck"},
            ]
            results = await _race_answers(migrated_db_url, debrief_id, answers)
            await _assert_winning_effects(pool, debrief_id, answers, results, before)
            statuses = [result["status"] for result in results]
            assert statuses.count("already_answered") == 1
            winner = next(
                index for index, status in enumerate(statuses) if status != "already_answered"
            )
            expected_state = "none_agreed" if winner == 0 else "captured"
            assert statuses[winner] == expected_state
            first = results[winner]
            state = await _debrief_state(pool, debrief_id)
            assert state["state"] == expected_state and state["answered_at"] is not None
            assert state["session_id"] == answers[winner]["session_id"]
        elif mode.startswith("stale-"):
            # Both rows were selected while pending. The second remains a genuine
            # pending-expiry positive; the first is answered after the job's read.
            _second_event, pending_id = await self._debrief(pool, "sam@example.test")
            await pool.execute("UPDATE meeting_debriefs SET prompted_at = NULL")
            await pool.execute(
                "UPDATE public.entities SET posture = 'no_contact' WHERE id = $1", sam
            )
            selected = asyncio.Event()
            release = asyncio.Event()
            real_active = debrief_job.active_entity_ids

            async def held_active(*args, **kwargs):
                active = await real_active(*args, **kwargs)
                assert str(sam) not in active
                selected.set()
                await release.wait()
                return active

            monkeypatch.setattr(debrief_job, "active_entity_ids", held_active)
            job = asyncio.create_task(
                debrief_job._propose_batch(pool, _Proposer(), datetime.now(UTC))
            )
            try:
                async with asyncio.timeout(15):
                    await _wait_until_held(job, selected)
                    first = await meeting_debrief_answer(
                        pool,
                        debrief_id=debrief_id,
                        commitments=(
                            [
                                {
                                    "summary": "Prepare my notes",
                                    "direction": "self",
                                    "counterparty_entity_id": None,
                                }
                            ]
                            if mode == "stale-captured-self"
                            else []
                        ),
                        session_id="answer-before-expiry",
                    )
                    expected_state = "captured" if mode == "stale-captured-self" else "none_agreed"
                    assert first["status"] == expected_state
                    state = await _debrief_state(pool, debrief_id)
                    effects = await _effects(pool)
                    release.set()
                    result = await job
                assert result["skipped"] == "nothing_to_ask"
                assert (await _debrief_state(pool, pending_id))["state"] == "expired"
                assert await _debrief_state(pool, debrief_id) == state
                assert await _effects(pool) == effects
            finally:
                release.set()
                if not job.done():
                    job.cancel()
                await asyncio.gather(job, return_exceptions=True)
        else:
            first = await meeting_debrief_answer(pool, debrief_id=debrief_id)
            state = await _debrief_state(pool, debrief_id)
            assert first["status"] == "none_agreed"
            assert await _commitments(pool) == []
            assert await pool.fetchval("SELECT state FROM meeting_debriefs") == "none_agreed"

        before_retry = await _effects(pool)
        second = await meeting_debrief_answer(
            pool, debrief_id=debrief_id, commitments=[{"summary": "Send Sam the deck"}]
        )

        assert second["status"] == "already_answered"
        assert await _effects(pool) == before_retry
        assert await _debrief_state(pool, debrief_id) == state

    @pytest.mark.parametrize("invalid_item", ["outside-attendee", "empty-normalized-action"])
    async def test_counterparty_outside_the_meeting_is_refused(self, pool, invalid_item) -> None:
        await _person(pool, "Sam Rivera", "sam@example.test")
        outsider = await _person(pool, "Priya Outsider", "priya@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")

        result = await meeting_debrief_answer(
            pool,
            debrief_id=debrief_id,
            commitments=[
                {"summary": "Send Sam the deck"},
                (
                    {"summary": "Call Priya", "counterparty_entity_id": str(outsider)}
                    if invalid_item == "outside-attendee"
                    else {"summary": "!!!"}
                ),
            ],
        )

        assert result["status"] == "invalid"
        assert await _commitments(pool) == [], "validated before the first write"
        assert await pool.fetchval("SELECT state FROM meeting_debriefs") == "pending"

    @pytest.mark.parametrize(
        "fault", ["later-create", "later-graph", "final-state", "cancellation", "connection-close"]
    )
    async def test_answer_fault_after_real_write_rolls_back_the_batch(
        self, pool, monkeypatch, fault
    ) -> None:
        await _person(pool, "Owner Person", "owner@example.test", owner=True)
        sam = await _person(pool, "Sam Rivera", "sam@example.test")
        _event_id, debrief_id = await self._debrief(pool, "sam@example.test")
        # The first item inserts a new episode. The second also exercises a real
        # confirmation UPDATE; its before image and graph upsert must roll back.
        prior = await commitment_tools.create_commitment(
            pool,
            source="relationship:commitment",
            summary="Confirm prior deck agreement",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=str(sam),
            confidence=0.9,
            evidence_opened={"source": "prior_synthetic_answer"},
            action_description="Confirm prior deck agreement",
        )
        assert prior is not None
        before = await _sentinels(pool, sam)
        state_before = await _debrief_state(pool, debrief_id)
        assert state_before == {"state": "pending", "answered_at": None, "session_id": None}
        real_graph = commitment_tools._post_write_commitment_edges
        answer_module = inspect.getmodule(meeting_debrief_answer)
        assert answer_module is not None
        real_create = answer_module.create_commitment
        graph_writes = []
        completed_creates = []
        held_connection = []
        reached = asyncio.Event()
        release = asyncio.Event()

        async def observed_graph(conn, transitions, **kwargs):
            await real_graph(conn, transitions, **kwargs)
            condition_id = transitions[0].condition_id
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.owner_conditions WHERE id = $1", condition_id
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM public.entity_graph_edges "
                    "WHERE source_schema = 'public' AND source_table = 'owner_conditions' "
                    "AND source_id = $1",
                    condition_id,
                )
                == 1
            )
            graph_writes.append(condition_id)
            if len(graph_writes) == 2:
                if fault == "later-graph":
                    raise RuntimeError("debrief-later-graph-fault after real SQL")
                if fault == "connection-close":
                    held_connection.append(conn)
                    reached.set()
                    await release.wait()

        async def observed_create(*args, **kwargs):
            if completed_creates and fault == "later-create":
                raise ValueError("debrief-later-create-fault after completed first call")
            result = await real_create(*args, **kwargs)
            completed_creates.append(result)
            if len(completed_creates) == 1 and fault == "cancellation":
                # Holding inside the old graph hook would precede its independent
                # COMMIT. Hold only AFTER the real public call has returned.
                reached.set()
                await release.wait()
            return result

        monkeypatch.setattr(commitment_tools, "_post_write_commitment_edges", observed_graph)
        monkeypatch.setattr(answer_module, "create_commitment", observed_create)
        if fault == "final-state":
            await pool.execute(
                """
                CREATE FUNCTION relationship.test_debrief_final_state_fault()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.state = 'captured' THEN
                        RAISE EXCEPTION 'debrief-final-state-fault after real writes';
                    END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER test_debrief_final_state_fault
                BEFORE UPDATE ON relationship.meeting_debriefs
                FOR EACH ROW EXECUTE FUNCTION relationship.test_debrief_final_state_fault();
                """
            )
        task = asyncio.create_task(
            meeting_debrief_answer(
                pool,
                debrief_id=debrief_id,
                commitments=[
                    {"summary": "Send a new project memo"},
                    {"summary": "Confirm prior deck agreement"},
                ],
                session_id=f"fault-{fault}",
            )
        )
        try:
            async with asyncio.timeout(15):
                if fault in {"cancellation", "connection-close"}:
                    await _wait_until_held(task, reached)
                    assert not task.done()
                    if fault == "cancellation":
                        assert len(completed_creates) == 1 and len(graph_writes) == 1
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        assert len(completed_creates) == 1 and len(graph_writes) == 2
                        await held_connection[0].close()
                        release.set()
                        with pytest.raises((asyncpg.InterfaceError, asyncpg.PostgresError)):
                            await task
                elif fault == "later-create":
                    result = await task
                    assert result["status"] == "invalid"
                    assert "debrief-later-create-fault" in result["reason"]
                elif fault == "later-graph":
                    with pytest.raises(RuntimeError, match="debrief-later-graph-fault"):
                        await task
                else:
                    with pytest.raises(asyncpg.RaiseError, match="debrief-final-state-fault"):
                        await task
            assert graph_writes[0] not in {
                row["id"] for row in before["public.owner_conditions"]
            }, "actual first condition INSERT and graph write were reached"
            assert len(graph_writes) == (1 if fault in {"later-create", "cancellation"} else 2)
            if len(graph_writes) == 2:
                assert graph_writes[1] == prior.condition_id, "actual confirmation SQL was reached"
            assert await _debrief_state(pool, debrief_id) == state_before
            assert await _effects(pool) == before, f"{fault}: pre-commit batch effects survived"
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if fault == "final-state":
                await pool.execute(
                    "DROP TRIGGER test_debrief_final_state_fault ON relationship.meeting_debriefs; "
                    "DROP FUNCTION relationship.test_debrief_final_state_fault()"
                )

    async def test_unresolved_attendee_yields_a_null_counterparty_commitment(self, pool) -> None:
        _event_id, debrief_id = await self._debrief(pool, "new.person@example.test")

        result = await meeting_debrief_answer(
            pool, debrief_id=debrief_id, commitments=[{"summary": "Send the deck"}]
        )

        assert result["status"] == "captured"
        (row,) = await _commitments(pool)
        assert row["metadata"]["counterparty_entity_id"] is None
