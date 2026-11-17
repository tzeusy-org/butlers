"""Switchboard inbox evidence to Relationship facts, with no session (bu-s11n0s.14, slice S2b).

Real migrated database, real Relationship role, real central writer. The inbox rows carry the
``raw_payload.control`` shape Switchboard ingest persists for a Gmail auto-reply or bounce.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.roster_imports import roster_module
from roster.relationship.tests.calendar_projection import insert_message, link_identity, make_entity

_facts = roster_module("relationship", "tools/automated_evidence_facts")
_jobs = roster_module("relationship", "jobs/relationship_jobs")

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_AWAY = "away-until"
_DEAD = "undeliverable-email"


async def _insert_automated_email(
    pool,
    *,
    sender: str,
    kind: str,
    evidence: dict[str, Any],
    received_at: datetime | None = None,
) -> uuid.UUID:
    control = {"policy_tier": "metadata", "automated_kind": kind, "automated_evidence": evidence}
    return await insert_message(
        pool,
        received_at=received_at or datetime.now(UTC) - timedelta(hours=1),
        request_context={"source_sender_identity": sender, "source_channel": "email"},
        direction="inbound",
        raw_payload={"control": control},
    )


def _auto_reply_evidence(away_until: str) -> dict[str, Any]:
    return {
        "original_sent": True,
        "sender_was_recipient": True,
        "sender_authenticated": True,
        "away_until": away_until,
        "failed_recipients": [],
    }


def _bounce_evidence(*failed: str, original_sent: bool = True) -> dict[str, Any]:
    return {
        "original_sent": original_sent,
        "sender_was_recipient": False,
        "sender_authenticated": True,
        "away_until": None,
        "failed_recipients": list(failed),
    }


async def _person_with_email(pool, address: str, *, roles: list[str] | None = None) -> uuid.UUID:
    entity_id = await make_entity(pool, roles=roles)
    await link_identity(pool, entity_id=entity_id, predicate="has-email", value=address)
    return entity_id


async def _active(pool, predicate: str) -> list[tuple[uuid.UUID, str]]:
    rows = await pool.fetch(
        "SELECT subject, object FROM relationship.entity_facts"
        " WHERE predicate = $1 AND validity = 'active' ORDER BY object",
        predicate,
    )
    return [(row["subject"], row["object"]) for row in rows]


def _in_days(days: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=1) + timedelta(days=days)).date().isoformat()


async def test_scheduled_job_writes_availability_and_never_spawns(interaction_sync_pool):
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    handler = get_deterministic_schedule_job_registry()["relationship"]["automated_evidence_sync"]
    async with interaction_sync_pool() as pool:
        ada = await _person_with_email(pool, "ada@example.test")
        back = _in_days(7)
        await _insert_automated_email(
            pool, sender="ada@example.test", kind="auto_reply", evidence=_auto_reply_evidence(back)
        )

        with patch(
            "butlers.core.spawner.Spawner.trigger",
            new=AsyncMock(side_effect=AssertionError("no session for automated evidence")),
        ) as trigger:
            result = await handler(pool, None)

        trigger.assert_not_awaited()
        assert result["errors"] == 0
        assert result["written"] == 1
        assert await _active(pool, _AWAY) == [(ada, back)]
        evidence = await pool.fetchval(
            "SELECT count(*) FROM relationship.fact_evidence fe"
            " JOIN relationship.entity_facts ef ON ef.id = fe.fact_id"
            " WHERE ef.predicate = $1",
            _AWAY,
        )
        assert evidence == 1


async def test_dsn_for_a_never_mailed_address_writes_no_fact(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _person_with_email(pool, "never@example.test")
        # Backscatter: the DSN names no original in the owner's SENT mailbox. Even a
        # malformed row that still lists the address must not become a fact.
        await _insert_automated_email(
            pool,
            sender="mailer-daemon@example.test",
            kind="bounce",
            evidence=_bounce_evidence("never@example.test", original_sent=False),
        )

        result = await _jobs.run_automated_evidence_sync(pool)

        assert result["errors"] == 0
        assert result["written"] == 0
        assert result["skipped_unbound"] == 1
        assert await _active(pool, _DEAD) == []


async def test_bound_dsn_marks_the_entity_address_undeliverable(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        bob = await _person_with_email(pool, "bob@example.test")
        await _insert_automated_email(
            pool,
            sender="mailer-daemon@googlemail.com",
            kind="bounce",
            evidence=_bounce_evidence("bob@example.test"),
        )

        result = await _jobs.run_automated_evidence_sync(pool)

        assert result["errors"] == 0
        assert await _active(pool, _DEAD) == [(bob, "bob@example.test")]


async def test_replaying_the_same_inbox_row_is_idempotent(interaction_sync_pool):
    from butlers.core.state import state_set

    STATE_KEY = _facts.STATE_KEY

    async with interaction_sync_pool() as pool:
        ada = await _person_with_email(pool, "ada@example.test")
        back = _in_days(3)
        received_at = datetime.now(UTC) - timedelta(hours=2)
        await _insert_automated_email(
            pool,
            sender="ada@example.test",
            kind="auto_reply",
            evidence=_auto_reply_evidence(back),
            received_at=received_at,
        )

        first = await _jobs.run_automated_evidence_sync(pool)
        # Rewind the checkpoint so the second run re-reads exactly the same row.
        await state_set(pool, STATE_KEY, (received_at - timedelta(days=1)).isoformat())
        second = await _jobs.run_automated_evidence_sync(pool)

        assert (first["written"], first["unchanged"]) == (1, 0)
        assert (second["written"], second["unchanged"]) == (0, 1)
        assert second["errors"] == 0
        assert await _active(pool, _AWAY) == [(ada, back)]
        rows = await pool.fetchval(
            "SELECT count(*) FROM relationship.entity_facts WHERE predicate = $1", _AWAY
        )
        assert rows == 1


async def test_an_address_two_entities_share_writes_nothing(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _person_with_email(pool, "shared@example.test")
        await _person_with_email(pool, "shared@example.test")
        await _insert_automated_email(
            pool,
            sender="shared@example.test",
            kind="auto_reply",
            evidence=_auto_reply_evidence(_in_days(5)),
        )

        result = await _jobs.run_automated_evidence_sync(pool)

        assert result["errors"] == 0
        assert result["skipped_unresolved"] == 1
        assert await _active(pool, _AWAY) == []


async def test_owner_and_unknown_senders_write_nothing(interaction_sync_pool):
    async with interaction_sync_pool() as pool:
        await _person_with_email(pool, "me@example.test", roles=["owner"])
        await _insert_automated_email(
            pool,
            sender="me@example.test",
            kind="auto_reply",
            evidence=_auto_reply_evidence(_in_days(5)),
        )
        await _insert_automated_email(
            pool,
            sender="stranger@example.test",
            kind="auto_reply",
            evidence=_auto_reply_evidence(_in_days(5)),
        )

        result = await _jobs.run_automated_evidence_sync(pool)

        assert result["errors"] == 0
        assert result["skipped_owner"] == 1
        assert result["skipped_unresolved"] == 1
        assert await _active(pool, _AWAY) == []
        parked = await pool.fetchval(
            "SELECT count(*) FROM relationship.pending_actions WHERE tool_name LIKE '%fact%'"
        )
        assert parked == 0
