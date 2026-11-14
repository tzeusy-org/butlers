"""Owner-chat commitment candidates on migrated PostgreSQL (bu-jv1moa.11).

REQ-commitment-lifecycle-011, -012 and -015. Spans come from real Switchboard
``ingest_v1`` writes read back through the real owner-chat projection; the
counterparty resolves through the production channel-identity path. Nothing on
the candidate path is stubbed. It lives here, not under ``roster/relationship``,
because it stages both butlers' schemas (Switchboard and Relationship run on
separate databases; the relay between them is tested in
``tests/core/test_service_plane_relay_endpoint.py``).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from butlers.core.commitments import create_commitment
from butlers.roster_imports import roster_module

cc = roster_module("relationship", "tools/commitment_candidates")
ingest_v1 = roster_module("switchboard", "tools/ingestion/ingest").ingest_v1
owner_chat_spans = roster_module("switchboard", "tools/owner_chat/projection").owner_chat_spans

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

OWNER = "6590000001@s.whatsapp.net"
SAM_JID = "6590000002@s.whatsapp.net"
STRANGER_JID = "6590000009@s.whatsapp.net"
PROMISE = "I'll send you the deck tomorrow"


@pytest.fixture
async def rel_pool(db) -> asyncpg.Pool:
    return await db("core", "memory", "relationship")


@pytest.fixture
async def sw_pool(db) -> asyncpg.Pool:
    return await db(
        "core", "switchboard", schemas={"switchboard": "switchboard"}, schema="switchboard"
    )


@pytest.fixture
async def sam(rel_pool: asyncpg.Pool) -> UUID:
    """A real contact whose phone matches the WhatsApp JID, made the butler's way."""
    contacts = roster_module("relationship", "tools/contacts")
    channel = roster_module("relationship", "tools/channel")
    contact = await contacts.contact_create(rel_pool, "Sam Rivera")
    await channel.channel_add(rel_pool, contact["id"], "phone", "+65 9000 0002")
    entity_id = await rel_pool.fetchval(
        "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", contact["id"]
    )
    assert entity_id is not None
    return entity_id


def _entry(message_id: str, sender: str, body: str, *, mine: bool, age: timedelta) -> dict:
    return {
        "message_id": message_id,
        "sender_identity": sender,
        "sender": "Owner" if mine else "Them",
        "text": body,
        "timestamp": (datetime.now(UTC) - age).isoformat(),
        "is_new": True,
        "reply_to": None,
        "is_from_me": mine,
        "forwarded": False,
    }


async def _chat(
    sw_pool: asyncpg.Pool,
    batch_id: str,
    owner_lines: list[tuple[str, str]],
    *,
    them: str = SAM_JID,
    age: timedelta = timedelta(minutes=10),
    history: list[dict] | None = None,
    group: bool = False,
) -> UUID:
    """Ingest one settled batch: a counterparty line, then the owner's lines."""
    if history is None:
        history = [_entry(f"{batch_id}-t", them, "Any news?", mine=False, age=age)]
        history += [_entry(mid, OWNER, body, mine=True, age=age) for mid, body in owner_lines]
    envelope = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "whatsapp_user_client",
            "provider": "whatsapp",
            "endpoint_identity": "whatsapp:+6590000001",
        },
        "event": {
            "external_event_id": batch_id,
            "external_conversation_id": f"whatsapp:{them}",
            "reply_target_ref": them,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": "multiple",
            "participants": {OWNER: "Owner", them: "Them"},
            "owner_sender_id": OWNER,
            "participant_count": 5 if group else 2,
            "chat_type": "group" if group else "private",
        },
        "payload": {
            "raw": {"conversation_history": history},
            "normalized_text": "\n".join(e["text"] for e in history),
        },
        "control": {
            "idempotency_key": f"wa_batch:{batch_id}",
            "policy_tier": "passive",
            "payload_type": "conversation_history",
        },
    }
    inbox_id = (await ingest_v1(sw_pool, envelope)).request_id
    # Age the row past the projection's 60-second in-flight window.
    received_at = await sw_pool.fetchval(
        "SELECT received_at FROM switchboard.message_inbox WHERE id = $1", inbox_id
    )
    target = received_at - timedelta(minutes=5)
    await sw_pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", target)
    await sw_pool.execute(
        "UPDATE switchboard.message_inbox SET received_at = $2 WHERE id = $1", inbox_id, target
    )
    return inbox_id


def _reader(sw_pool: asyncpg.Pool):
    async def read(cursor: Mapping[str, str] | None) -> Mapping[str, Any]:
        return await owner_chat_spans(
            sw_pool,
            after_received_at=None
            if cursor is None
            else datetime.fromisoformat(cursor["received_at"]),
            after_inbox_id=None if cursor is None else UUID(cursor["inbox_id"]),
        )

    return read


async def _scan(rel_pool: asyncpg.Pool, sw_pool: asyncpg.Pool) -> dict[str, Any]:
    return await cc.scan(rel_pool, read_spans=_reader(sw_pool), live=True)


async def _rows(rel_pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await rel_pool.fetch(
        "SELECT * FROM relationship.commitment_candidates ORDER BY created_at"
    )
    return [dict(r) for r in rows]


def _evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row["evidence_ref"]
    return json.loads(value) if isinstance(value, str) else dict(value)


async def test_a_verified_owner_promise_creates_one_candidate_without_its_text(
    rel_pool, sw_pool, sam
) -> None:
    inbox_id = await _chat(sw_pool, "b-promise", [("m-1", PROMISE)])

    result = await _scan(rel_pool, sw_pool)

    assert result["status"] == "ok" and result["outcomes"] == {"created": 1}
    (row,) = await _rows(rel_pool)
    assert row["state"] == "pending"
    assert (row["kind"], row["direction"], row["counterparty_entity_id"]) == (
        "promise",
        "owner_to_other",
        sam,
    )
    assert row["action_description"] == "send you the deck"
    assert _evidence(row) == {"ingestion_event_id": str(inbox_id), "provider_message_id": "m-1"}
    assert row["taint"] == "untrusted_origin"
    assert row["lapse_at"] - row["event_time"] == timedelta(days=14)
    assert PROMISE not in json.dumps({k: str(v) for k, v in row.items()})
    assert await rel_pool.fetchval("SELECT count(*) FROM public.owner_conditions") == 0


async def test_group_counterparty_authored_and_forwarded_promises_create_nothing(
    rel_pool, sw_pool, sam
) -> None:
    age = timedelta(minutes=10)
    await _chat(
        sw_pool,
        "b-mixed",
        [],
        history=[
            _entry("m-sam", SAM_JID, PROMISE, mine=False, age=age),
            {**_entry("m-fwd", OWNER, PROMISE, mine=True, age=age), "forwarded": True},
        ],
    )
    await _chat(sw_pool, "b-group", [("m-g", PROMISE)], group=True)

    result = await _scan(rel_pool, sw_pool)

    assert result["status"] == "ok" and result["outcomes"] == {}
    assert await _rows(rel_pool) == []


async def test_unresolved_counterparties_and_non_promises_create_nothing(
    rel_pool, sw_pool, sam
) -> None:
    await _chat(sw_pool, "b-stranger", [("m-s", PROMISE)], them=STRANGER_JID)
    await _chat(sw_pool, "b-hedge", [("m-h", "I should probably send you the deck")])

    result = await _scan(rel_pool, sw_pool)

    assert result["outcomes"] == {"counterparty_unknown": 1, "no_commitment": 1}
    assert await _rows(rel_pool) == []


async def test_an_active_commitment_and_a_live_candidate_suppress_duplicates(
    rel_pool, sw_pool, sam
) -> None:
    await create_commitment(
        rel_pool,
        source="relationship:commitment",
        summary="send the book",
        kind="promise",
        direction="owner_to_other",
        counterparty_entity_id=str(sam),
        confidence=0.9,
        evidence_opened={"source": "conversation_extraction"},
        action_description="send you the book",
    )
    await _chat(
        sw_pool,
        "b-dupes",
        [
            ("m-book", "I'll send you the book tomorrow"),
            ("m-deck-1", PROMISE),
            ("m-deck-2", "I'll send you the deck on Friday"),
        ],
    )

    result = await _scan(rel_pool, sw_pool)

    assert result["outcomes"] == {"already_tracked": 1, "created": 1, "duplicate": 1}
    (row,) = await _rows(rel_pool)
    assert _evidence(row)["provider_message_id"] == "m-deck-1"


async def test_replay_crash_before_cursor_and_concurrent_runs_converge(
    rel_pool, sw_pool, sam
) -> None:
    await _chat(sw_pool, "b-replay", [("m-r", PROMISE)])

    await asyncio.gather(_scan(rel_pool, sw_pool), _scan(rel_pool, sw_pool))
    # A crash before the cursor write is the same as forgetting the cursor.
    await rel_pool.execute("DELETE FROM state WHERE key = $1", cc.CURSOR_KEY)
    replay = await _scan(rel_pool, sw_pool)

    assert replay["outcomes"] == {"seen": 1}
    assert len(await _rows(rel_pool)) == 1


async def test_while_dark_nothing_is_read_and_no_cursor_is_written(rel_pool, sw_pool, sam) -> None:
    await _chat(sw_pool, "b-dark", [("m-d", PROMISE)])

    result = await cc.scan(rel_pool, read_spans=_reader(sw_pool), live=False)

    assert result["status"] == "skipped"
    assert await _rows(rel_pool) == []
    assert await rel_pool.fetchval("SELECT count(*) FROM state WHERE key = $1", cc.CURSOR_KEY) == 0


async def _live_row(rel_pool: asyncpg.Pool, key: str, *, event_age: timedelta, lapse_in: timedelta):
    await rel_pool.execute(
        """
        INSERT INTO relationship.commitment_candidates (
            source_key, state, kind, direction, counterparty_entity_id, action_description,
            confidence, marker, fingerprint, channel, evidence_ref, taint, event_time, lapse_at)
        VALUES ($1, 'pending', 'promise', 'owner_to_other', gen_random_uuid(), 'call you',
                0.9, 'i''ll', $1, 'whatsapp_user_client', '{}'::jsonb, 'untrusted_origin',
                now() - $2::interval, now() + $3::interval)
        """,
        key,
        event_age,
        lapse_in,
    )


async def test_lapse_is_silent_and_clears_content_and_old_tombstones_purge(rel_pool) -> None:
    await _live_row(rel_pool, "a" * 64, event_age=timedelta(days=5), lapse_in=-timedelta(hours=1))
    await _live_row(rel_pool, "b" * 64, event_age=timedelta(days=5), lapse_in=timedelta(days=9))
    await rel_pool.execute(
        """
        INSERT INTO relationship.commitment_candidates
            (source_key, state, event_time, lapse_at, terminal_at)
        VALUES ($1, 'done', now() - interval '15 days', now() - interval '1 day', now())
        """,
        "c" * 64,
    )

    counts = await cc.lapse_and_purge(rel_pool)

    assert counts == {"lapsed": 1, "purged": 1}
    rows = {r["source_key"][0]: r for r in await _rows(rel_pool)}
    assert set(rows) == {"a", "b"}
    lapsed = rows["a"]
    assert lapsed["state"] == "lapsed" and lapsed["terminal_at"] is not None
    for column in CONTENT_COLUMNS:
        assert lapsed[column] is None, column
    assert rows["b"]["state"] == "pending"
    assert await rel_pool.fetchval("SELECT count(*) FROM public.owner_conditions") == 0


CONTENT_COLUMNS = (
    "kind",
    "direction",
    "counterparty_entity_id",
    "action_description",
    "deadline",
    "confidence",
    "marker",
    "fingerprint",
    "channel",
    "evidence_ref",
    "taint",
    "card_snapshot",
    "card_expires_at",
)


@pytest.mark.parametrize(
    "statement",
    [
        # A terminal row may not keep content.
        """INSERT INTO relationship.commitment_candidates
           (source_key, state, event_time, lapse_at, terminal_at, action_description)
           VALUES (repeat('d', 64), 'done', now(), now() + interval '1 day', now(), 'x')""",
        # A terminal row needs its terminal time.
        """INSERT INTO relationship.commitment_candidates
           (source_key, state, event_time, lapse_at)
           VALUES (repeat('d', 64), 'ignored', now(), now() + interval '1 day')""",
        # Only a tracked tombstone keeps the fingerprint, and a tracked one must.
        """INSERT INTO relationship.commitment_candidates
           (source_key, state, event_time, lapse_at, terminal_at)
           VALUES (repeat('d', 64), 'tracked', now(), now() + interval '1 day', now())""",
        # A live row needs its content.
        """INSERT INTO relationship.commitment_candidates
           (source_key, state, event_time, lapse_at)
           VALUES (repeat('d', 64), 'pending', now(), now() + interval '1 day')""",
        # The action is at most 120 characters.
        """INSERT INTO relationship.commitment_candidates (
            source_key, state, kind, direction, counterparty_entity_id, action_description,
            confidence, marker, fingerprint, channel, evidence_ref, taint, event_time, lapse_at)
           VALUES (repeat('d', 64), 'pending', 'promise', 'owner_to_other', gen_random_uuid(),
                   repeat('x', 121), 0.9, 'm', 'f', 'whatsapp_user_client', '{}',
                   'untrusted_origin', now(), now() + interval '1 day')""",
    ],
    ids=["terminal-content", "terminal-time", "tracked-fingerprint", "live-content", "long"],
)
async def test_the_store_refuses_content_on_tombstones_and_malformed_rows(
    rel_pool, statement: str
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await rel_pool.execute(statement)
