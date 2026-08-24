"""Commitment extraction against a real ledger (bu-s208f, REQ-commitment-lifecycle-007/008).

``test_commitment_extraction.py`` owns the predicate — what counts as an
explicit commitment. This file owns everything the predicate cannot decide
alone: that the counterparty resolves through the *production* entity path
(``contact_resolve`` -> ``contact_entity_map`` -> ``public.entities``), that a
yes lands one commitment-class row with the metadata REQ-commitment-lifecycle-001
defines, that a no lands nothing at all, and that a closure carries the
``evidence_closed`` receipt REQ-commitment-lifecycle-008 makes mandatory.

Nothing here is stubbed. The entity resolution is the real one — an
unresolvable counterparty test that mocked the resolver would prove nothing
about production — so these run the core, memory, and relationship Alembic
chains against a real Postgres.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.relationship.commitments import (
    COMMITMENT_SOURCE,
    capture_commitment,
    capture_completion,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# A Monday, so "tomorrow" is unambiguously the 25th.
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)

_TRUNCATE_TABLES = [
    "public.owner_conditions",
    "public.contact_entity_map",
    "public.entities",
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Core + memory + relationship, all chains landing in ``public``.

    Matches ``tests/features/test_vcard.py``: the relationship chain runs
    without a schema override so unqualified names resolve without a
    search_path, which is what ``contact_resolve`` and
    ``resolve_contact_entity_id`` expect of a flat test topology.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    for table in _TRUNCATE_TABLES:
        await p.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608
    yield p
    await p.close()


@pytest.fixture
async def sam_entity_id(pool) -> str:
    """A real contact, created the way the Relationship butler creates one."""
    from butlers.tools.relationship import contact_create

    contact = await contact_create(pool, "Sam Rivera")
    entity_id = await pool.fetchval(
        "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", contact["id"]
    )
    assert entity_id is not None
    return str(entity_id)


async def _commitment_rows(pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT source, fingerprint, state, summary, metadata
        FROM public.owner_conditions
        WHERE metadata->>'class' = 'commitment'
        ORDER BY first_detected_at
        """
    )
    decoded = []
    for row in rows:
        item = dict(row)
        if isinstance(item["metadata"], str):
            item["metadata"] = json.loads(item["metadata"])
        decoded.append(item)
    return decoded


class TestExplicitPromise:
    async def test_req_commitment_lifecycle_007_explicit_promise_creates_a_commitment(
        self, pool, sam_entity_id
    ) -> None:
        """The spec's scenario, end to end: promise, owner_to_other, Sam, tomorrow."""
        result = await capture_commitment(
            pool,
            utterance="I'll send Sam that book tomorrow.",
            session_id="session-alpha",
            now=NOW,
        )

        assert result["status"] == "created"

        rows = await _commitment_rows(pool)
        assert len(rows) == 1
        metadata = rows[0]["metadata"]
        assert rows[0]["source"] == COMMITMENT_SOURCE
        assert rows[0]["state"] == "open"
        assert metadata["kind"] == "promise"
        assert metadata["direction"] == "owner_to_other"
        assert metadata["confidence"] >= 0.8
        assert metadata["deadline"].startswith("2026-08-25")

    async def test_req_commitment_lifecycle_007_counterparty_resolves_to_an_entity_id(
        self, pool, sam_entity_id
    ) -> None:
        """The anchor is the same entity the rest of the butler would use."""
        result = await capture_commitment(
            pool, utterance="I'll send Sam that book tomorrow.", now=NOW
        )

        assert result["counterparty_entity_id"] == sam_entity_id
        rows = await _commitment_rows(pool)
        assert rows[0]["metadata"]["counterparty_entity_id"] == sam_entity_id

    async def test_req_commitment_lifecycle_007_creation_records_opening_evidence(
        self, pool, sam_entity_id
    ) -> None:
        await capture_commitment(
            pool,
            utterance="I'll send Sam that book tomorrow.",
            session_id="session-alpha",
            now=NOW,
        )

        evidence = (await _commitment_rows(pool))[0]["metadata"]["evidence_opened"]
        assert evidence["source"] == "conversation_extraction"
        assert evidence["session_id"] == "session-alpha"
        assert evidence["utterance"] == "I'll send Sam that book tomorrow."

    async def test_req_commitment_lifecycle_007_restating_the_promise_confirms_in_place(
        self, pool, sam_entity_id
    ) -> None:
        """A second telling is the same commitment (REQ-commitment-lifecycle-002)."""
        first = await capture_commitment(
            pool, utterance="I'll send Sam that book tomorrow.", now=NOW
        )
        second = await capture_commitment(pool, utterance="I'll send Sam that book today.", now=NOW)

        assert first["status"] == "created"
        assert second["status"] == "confirmed"
        assert len(await _commitment_rows(pool)) == 1


class TestNoCommitment:
    async def test_req_commitment_lifecycle_007_ambiguous_statement_creates_nothing(
        self, pool, sam_entity_id
    ) -> None:
        """The spec's counter-example, checked against the table, not the predicate."""
        result = await capture_commitment(
            pool, utterance="I should probably get around to calling Sam.", now=NOW
        )

        assert result == {"status": "skipped", "reason": "no_commitment_pattern"}
        assert await _commitment_rows(pool) == []

    async def test_req_commitment_lifecycle_007_unresolvable_counterparty_creates_nothing(
        self, pool, sam_entity_id, caplog
    ) -> None:
        """Real resolution, real miss: nobody named Kaito exists in this database."""
        with caplog.at_level(logging.WARNING, logger="butlers.tools.relationship.commitments"):
            result = await capture_commitment(
                pool, utterance="I'll send Kaito that book tomorrow.", now=NOW
            )

        assert result["status"] == "skipped"
        assert result["reason"] == "counterparty_unresolved"
        assert result["candidates"] == ["Kaito"]
        assert await _commitment_rows(pool) == []
        assert any("no counterparty resolved" in record.message for record in caplog.records)


class TestResolutionFromConversation:
    async def test_req_commitment_lifecycle_007_completion_resolves_the_commitment(
        self, pool, sam_entity_id
    ) -> None:
        await capture_commitment(
            pool, utterance="I'll send Sam that book tomorrow.", session_id="s1", now=NOW
        )

        result = await capture_completion(pool, utterance="I sent Sam the book.", session_id="s2")

        assert result["status"] == "resolved"
        assert result["resolution_reason"] == "satisfied"
        rows = await _commitment_rows(pool)
        assert [row["state"] for row in rows] == ["resolved"]

    async def test_req_commitment_lifecycle_008_resolution_records_closing_evidence(
        self, pool, sam_entity_id
    ) -> None:
        """No silent resolution: session id and utterance provenance both land."""
        await capture_commitment(
            pool, utterance="I'll send Sam that book tomorrow.", session_id="s1", now=NOW
        )
        await capture_completion(pool, utterance="I sent Sam the book.", session_id="s2")

        metadata = (await _commitment_rows(pool))[0]["metadata"]
        assert metadata["resolution_reason"] == "satisfied"
        assert metadata["evidence_closed"]["source"] == "owner_confirmed"
        assert metadata["evidence_closed"]["session_id"] == "s2"
        assert metadata["evidence_closed"]["utterance"] == "I sent Sam the book."
        # REQ-commitment-lifecycle-001: closing evidence never displaces opening.
        assert metadata["evidence_opened"]["session_id"] == "s1"

    async def test_req_commitment_lifecycle_008_unrelated_completion_resolves_nothing(
        self, pool, sam_entity_id
    ) -> None:
        """The negative direction for closure: a different action leaves it open."""
        await capture_commitment(
            pool, utterance="I'll send Sam that book tomorrow.", session_id="s1", now=NOW
        )

        result = await capture_completion(pool, utterance="I paid Sam the deposit.")

        assert result == {"status": "skipped", "reason": "no_matching_commitment"}
        assert [row["state"] for row in await _commitment_rows(pool)] == ["open"]

    async def test_req_commitment_lifecycle_008_completion_picks_the_matching_commitment(
        self, pool, sam_entity_id
    ) -> None:
        """Two live promises to the same person; only the one reported done closes."""
        await capture_commitment(
            pool, utterance="I'll send Sam that book tomorrow.", session_id="s1", now=NOW
        )
        await capture_commitment(
            pool, utterance="I'll book the table for Sam tonight.", session_id="s1", now=NOW
        )

        result = await capture_completion(pool, utterance="I sent Sam the book.")

        assert result["status"] == "resolved"
        states = {row["summary"]: row["state"] for row in await _commitment_rows(pool)}
        assert states["Send Sam that book"] == "resolved"
        assert states["Book the table for Sam"] == "open"
