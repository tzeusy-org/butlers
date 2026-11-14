"""Owner-chat projection on migrated PostgreSQL, fed by real ``ingest_v1`` writes.

REQ-butler-switchboard-011: qualifying selection, group and owner-mismatch exclusion,
cursor resume without gaps, the 60-second in-flight exclusion for a late-committing
row. Unavailability as an error is in ``test_owner_chat_projection.py``; caller
authentication is the relay's and is tested with the relay registry.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from butlers.roster_imports import roster_module

ingest_v1 = roster_module("switchboard", "tools/ingestion/ingest").ingest_v1
owner_chat_spans = roster_module("switchboard", "tools/owner_chat/projection").owner_chat_spans

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

OWNER = "6590000001@s.whatsapp.net"
THEM = "6590000002@s.whatsapp.net"


@pytest.fixture
async def switchboard_pool(db):
    return await db(
        "core", "switchboard", schemas={"switchboard": "switchboard"}, schema="switchboard"
    )


def _entry(message_id: str, sender: str, body: str, *, mine: bool, fwd: bool) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "sender_identity": sender,
        "sender": "Owner" if sender == OWNER else "Them",
        "text": body,
        "timestamp": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        "is_new": True,
        "reply_to": None,
        "is_from_me": mine,
        "forwarded": fwd,
    }


def _batch(
    batch_id: str,
    history: list[dict[str, Any]],
    *,
    chat_type: str = "private",
    participant_count: int = 2,
    owner: str = OWNER,
) -> dict[str, Any]:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "whatsapp_user_client",
            "provider": "whatsapp",
            "endpoint_identity": "whatsapp:+6590000001",
        },
        "event": {
            "external_event_id": batch_id,
            "external_conversation_id": f"whatsapp:{batch_id}-chat",
            "reply_target_ref": f"{batch_id}-chat",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": "multiple",
            "participants": {OWNER: "Owner", THEM: "Them"},
            "owner_sender_id": owner,
            "participant_count": participant_count,
            "chat_type": chat_type,
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


async def _ingest(pool: asyncpg.Pool, envelope: dict[str, Any]) -> UUID:
    result = await ingest_v1(pool, envelope)
    return result.request_id


async def _age(pool: asyncpg.Pool, inbox_id: UUID, seconds: int) -> None:
    """Move one row's received_at back, as if its ingest had committed that long ago."""
    received_at = await pool.fetchval(
        "SELECT received_at FROM switchboard.message_inbox WHERE id = $1", inbox_id
    )
    target = received_at - timedelta(seconds=seconds)
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", target)
    await pool.execute(
        "UPDATE switchboard.message_inbox SET received_at = $2 WHERE id = $1", inbox_id, target
    )


async def _all_spans(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    page = await owner_chat_spans(pool, after_received_at=None, after_inbox_id=None)
    return page["spans"]


async def test_only_the_owners_own_unforwarded_entry_is_projected(
    switchboard_pool: asyncpg.Pool,
) -> None:
    inbox_id = await _ingest(
        switchboard_pool,
        _batch(
            "b-select",
            [
                _entry("m-them", THEM, "Can you send the deck?", mine=False, fwd=False),
                _entry("m-owner", OWNER, "I'll send it tomorrow", mine=True, fwd=False),
                _entry("m-fwd", OWNER, "I will pay you back", mine=True, fwd=True),
            ],
        ),
    )
    await _age(switchboard_pool, inbox_id, 120)

    (span,) = await _all_spans(switchboard_pool)

    assert span["provider_message_id"] == "m-owner"
    assert span["counterparty_identity"] == THEM
    assert span["ingestion_event_id"] == str(inbox_id)
    assert span["text"] == "I'll send it tomorrow"


async def test_group_chats_and_owner_mismatch_are_excluded(
    switchboard_pool: asyncpg.Pool,
) -> None:
    group = await _ingest(
        switchboard_pool,
        _batch(
            "b-group",
            [_entry("m-g", OWNER, "I'll bring the cake", mine=True, fwd=False)],
            chat_type="group",
            participant_count=5,
        ),
    )
    mismatch = await _ingest(
        switchboard_pool,
        _batch(
            "b-mismatch",
            [_entry("m-x", THEM, "I'll call you", mine=True, fwd=False)],
        ),
    )
    await _age(switchboard_pool, group, 120)
    await _age(switchboard_pool, mismatch, 120)

    assert await _all_spans(switchboard_pool) == []


async def test_the_cursor_resumes_without_gaps(switchboard_pool: asyncpg.Pool) -> None:
    ids = []
    for n in range(3):
        inbox_id = await _ingest(
            switchboard_pool,
            _batch(
                f"b-cur-{n}",
                [
                    _entry(f"t-{n}", THEM, "ok?", mine=False, fwd=False),
                    _entry(f"o-{n}", OWNER, f"I'll do part {n}", mine=True, fwd=False),
                ],
            ),
        )
        ids.append(inbox_id)
    for n, inbox_id in enumerate(ids):
        await _age(switchboard_pool, inbox_id, 300 - n)

    first = await owner_chat_spans(switchboard_pool, after_received_at=None, after_inbox_id=None)
    cursor = first["cursor"]
    assert [s["provider_message_id"] for s in first["spans"]] == ["o-0", "o-1", "o-2"]

    late = await _ingest(
        switchboard_pool,
        _batch(
            "b-cur-late",
            [
                _entry("t-late", THEM, "and?", mine=False, fwd=False),
                _entry("o-late", OWNER, "I'll do the rest", mine=True, fwd=False),
            ],
        ),
    )
    await _age(switchboard_pool, late, 90)

    second = await owner_chat_spans(
        switchboard_pool,
        after_received_at=datetime.fromisoformat(cursor["received_at"]),
        after_inbox_id=UUID(cursor["inbox_id"]),
    )
    assert [s["provider_message_id"] for s in second["spans"]] == ["o-late"]

    empty = await owner_chat_spans(
        switchboard_pool,
        after_received_at=datetime.fromisoformat(second["cursor"]["received_at"]),
        after_inbox_id=UUID(second["cursor"]["inbox_id"]),
    )
    assert empty["spans"] == [] and empty["cursor"] == second["cursor"]


async def test_a_late_committing_row_is_not_skipped(switchboard_pool: asyncpg.Pool) -> None:
    """Rows inside the 60-second window are not read; the cursor cannot pass them."""
    first = await _ingest(
        switchboard_pool,
        _batch(
            "b-late-1",
            [
                _entry("t-a", THEM, "?", mine=False, fwd=False),
                _entry("o-a", OWNER, "I'll check", mine=True, fwd=False),
            ],
        ),
    )
    second = await _ingest(
        switchboard_pool,
        _batch(
            "b-late-2",
            [
                _entry("t-b", THEM, "?", mine=False, fwd=False),
                _entry("o-b", OWNER, "I'll confirm", mine=True, fwd=False),
            ],
        ),
    )
    # first's received_at is T (40 s ago); second's is later (20 s ago).
    await _age(switchboard_pool, first, 40)
    await _age(switchboard_pool, second, 20)

    early = await owner_chat_spans(switchboard_pool, after_received_at=None, after_inbox_id=None)
    assert early["spans"] == [] and early["cursor"] is None

    await _age(switchboard_pool, first, 60)
    await _age(switchboard_pool, second, 60)
    later = await owner_chat_spans(switchboard_pool, after_received_at=None, after_inbox_id=None)
    assert [s["provider_message_id"] for s in later["spans"]] == ["o-a", "o-b"]
