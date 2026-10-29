"""Real-Postgres posture refusal for meeting_debrief_answer (bu-q7vx1q.69).

A counterparty whose posture stopped being ``active`` between the prompt and the answer
refuses the whole answer: ``invalid``, no commitment, debrief still answerable. People are
synthetic; the helpers mirror ``test_meeting_debrief_db.py`` so this file stands alone.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.jobs.meeting_debrief import run_meeting_debrief
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.meeting_debrief import meeting_debrief_answer

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
    async def __call__(self, pool, **kwargs):
        return {"status": "accepted", "reason": "scripted"}


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


async def _prompted_debrief(pool, email: str) -> str:
    src = await _source(pool)
    event_id = await _event(pool, src, "Roadmap sync", attendees=_attendees(email))
    await run_meeting_debrief(pool, insight_proposer=_Proposer())
    return str(await pool.fetchval("SELECT id FROM meeting_debriefs WHERE event_id = $1", event_id))


@pytest.mark.parametrize("posture", ["memorial", "quiet", "no_contact"])
async def test_answer_naming_a_no_longer_active_counterparty_is_refused(pool, posture) -> None:
    await _person(pool, "Owner Person", "owner@example.test", owner=True)
    sam = await _person(pool, "Sam Rivera", "sam@example.test")
    debrief_id = await _prompted_debrief(pool, "sam@example.test")
    await pool.execute("UPDATE public.entities SET posture = $2 WHERE id = $1", sam, posture)

    result = await meeting_debrief_answer(
        pool,
        debrief_id=debrief_id,
        commitments=[{"summary": "Send Sam the deck", "counterparty_entity_id": str(sam)}],
    )

    assert result["status"] == "invalid"
    assert posture not in result["reason"] and "posture" not in result["reason"]
    assert await _commitments(pool) == []
    state = await pool.fetchrow(
        "SELECT state, answered_at FROM meeting_debriefs WHERE id = $1", uuid.UUID(debrief_id)
    )
    assert state["state"] == "pending" and state["answered_at"] is None


async def test_reanswering_without_the_refused_item_is_captured(pool) -> None:
    await _person(pool, "Owner Person", "owner@example.test", owner=True)
    sam = await _person(pool, "Sam Rivera", "sam@example.test")
    debrief_id = await _prompted_debrief(pool, "sam@example.test")
    await pool.execute("UPDATE public.entities SET posture = 'memorial' WHERE id = $1", sam)

    refused = await meeting_debrief_answer(
        pool,
        debrief_id=debrief_id,
        commitments=[{"summary": "Send Sam the deck", "counterparty_entity_id": str(sam)}],
    )
    assert refused["status"] == "invalid"

    retry = await meeting_debrief_answer(
        pool,
        debrief_id=debrief_id,
        commitments=[{"summary": "Book the room", "direction": "self"}],
    )

    assert retry["status"] == "captured"
    assert {row["summary"] for row in await _commitments(pool)} == {"Book the room"}
