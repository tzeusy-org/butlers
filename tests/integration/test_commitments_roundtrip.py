"""Real-Postgres regression: the commitment helper module (bu-j87m4, RFC 0026).

tests/core/test_commitments.py already pins the fingerprint recipe, the
metadata this module builds, and every rejection that happens before a pool
connection. Those are decisions; this file covers the consequences that only
a real ledger can show: that an equivalent restatement confirms the existing
episode instead of inserting a second row, that a resolution adds closing
evidence without displacing creation evidence, and that the commitment-class
query surface matches what the metadata convention claims it matches.

It does not re-derive the condition ledger's own open/aging/escalate/resolve
proofs — tests/integration/test_owner_conditions_roundtrip.py owns those.
"""

from __future__ import annotations

import json
import shutil
import uuid

import asyncpg
import pytest

from butlers.core.commitments import (
    commitment_fingerprint,
    create_commitment,
    list_active_commitments,
    list_entity_commitments,
    resolve_commitment,
)
from butlers.core.owner_conditions import Observation, reconcile_snapshot
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=2, max_size=10)
    yield p
    await p.close()


@pytest.fixture
def source() -> str:
    """A per-test source so concurrent ledger rows never cross-contaminate."""
    return f"relationship:commitment-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def entity_id() -> str:
    return str(uuid.uuid4())


def _evidence(session: str) -> dict[str, str]:
    return {
        "source": "conversation",
        "session_id": session,
        "excerpt": "synthetic test utterance",
    }


def _metadata(row: asyncpg.Record) -> dict:
    """Decode a row's JSONB metadata regardless of whether a codec is registered.

    The ``pool`` fixture registers none, so asyncpg hands back JSONB as text;
    the guard mirrors tests/integration/test_owner_conditions_roundtrip.py so
    this file does not silently depend on that.
    """
    raw = row["metadata"]
    return json.loads(raw) if isinstance(raw, str) else raw


async def _rows_for(pool: asyncpg.Pool, source: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, fingerprint, episode, state, first_detected_at, last_confirmed_at, "
        "metadata FROM public.owner_conditions WHERE source = $1 ORDER BY episode",
        source,
    )


class TestCommitmentCreation:
    async def test_req_commitment_lifecycle_001_persists_the_metadata_convention(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        transition = await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-open"),
            action_description="send Sam the book",
            deadline="2026-09-01T00:00:00+00:00",
        )

        assert transition is not None
        assert transition.transition == "opened"
        assert transition.episode == 1
        assert transition.escalation_level == "L0"

        rows = await _rows_for(pool, source)
        assert len(rows) == 1
        metadata = _metadata(rows[0])
        assert metadata["class"] == "commitment"
        assert metadata["kind"] == "promise"
        assert metadata["direction"] == "owner_to_other"
        assert metadata["counterparty_entity_id"] == entity_id
        assert metadata["confidence"] == 0.9
        assert metadata["deadline"] == "2026-09-01T00:00:00+00:00"
        assert metadata["evidence_opened"]["session_id"] == "session-open"
        assert metadata["identity_payload"]["version"] == 1

    async def test_req_commitment_lifecycle_003_an_equivalent_restatement_confirms_in_place(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        """The two phrasings differ as strings; only normalization makes them one commitment."""
        spoken = "Send Sam the book, tomorrow!"
        restated = "  send   SAM the  book — tomorrow "
        assert spoken != restated

        opened = await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description=spoken,
        )
        first_rows = await _rows_for(pool, source)

        confirmed = await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book (restated)",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-2"),
            action_description=restated,
        )

        assert opened is not None and confirmed is not None
        assert confirmed.transition == "confirmed"
        assert confirmed.condition_id == opened.condition_id
        assert confirmed.episode == 1

        rows = await _rows_for(pool, source)
        assert len(rows) == 1, "an equivalent restatement must not fork the commitment"
        assert rows[0]["last_confirmed_at"] > first_rows[0]["last_confirmed_at"]
        assert rows[0]["first_detected_at"] == first_rows[0]["first_detected_at"]

    async def test_req_commitment_lifecycle_003_a_different_action_coexists(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        common = {
            "source": source,
            "kind": "promise",
            "direction": "owner_to_other",
            "counterparty_entity_id": entity_id,
            "confidence": 0.9,
            "evidence_opened": _evidence("session-1"),
        }
        book = await create_commitment(
            pool, summary="Send the book", action_description="send Sam the book", **common
        )
        call = await create_commitment(
            pool,
            summary="Call about the book",
            action_description="call Sam about the book",
            **common,
        )

        assert book is not None and call is not None
        assert book.fingerprint != call.fingerprint
        assert call.transition == "opened"

        rows = await _rows_for(pool, source)
        assert len(rows) == 2
        assert {row["state"] for row in rows} == {"open"}

    async def test_req_commitment_lifecycle_004_low_confidence_writes_nothing(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        result = await create_commitment(
            pool,
            source=source,
            summary="Maybe call Sam",
            kind="follow_up",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.5,
            evidence_opened=_evidence("session-hedged"),
            action_description="probably call Sam sometime",
        )

        assert result is None
        assert await _rows_for(pool, source) == []

    @pytest.mark.parametrize("confidence", [0.6, 0.7, 0.79, 0.8, 0.95])
    async def test_req_commitment_lifecycle_004_every_created_band_is_queryable(
        self, pool: asyncpg.Pool, source: str, entity_id: str, confidence: float
    ) -> None:
        """Medium and high confidence differ only in surfacing, which is not this module's job."""
        await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=confidence,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )

        active = await list_active_commitments(pool, source=source)
        assert [row["metadata"]["confidence"] for row in active] == [confidence]

        by_entity = await list_entity_commitments(pool, entity_id=entity_id)
        assert [row["metadata"]["confidence"] for row in by_entity] == [confidence]


class TestCommitmentResolution:
    async def test_req_commitment_lifecycle_001_resolution_adds_evidence_without_displacing_it(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-open"),
            action_description="send Sam the book",
        )
        fingerprint = commitment_fingerprint(
            source=source,
            counterparty_entity_id=entity_id,
            action_description="send Sam the book",
        )

        resolved = await resolve_commitment(
            pool,
            source=source,
            fingerprint=fingerprint,
            resolution_reason="satisfied",
            evidence_closed={
                "source": "owner_confirmed",
                "session_id": "session-close",
                "detail": "Owner said: I sent it",
            },
        )

        assert resolved is not None
        assert resolved.transition == "resolved"
        assert resolved.resolved_at is not None
        assert resolved.recovered_after_s >= 0

        rows = await _rows_for(pool, source)
        metadata = _metadata(rows[0])
        assert rows[0]["state"] == "resolved"
        assert metadata["evidence_opened"]["session_id"] == "session-open"
        assert metadata["evidence_closed"]["session_id"] == "session-close"
        assert metadata["resolution_reason"] == "satisfied"
        assert metadata["class"] == "commitment"
        assert metadata["confidence"] == 0.9

    async def test_req_commitment_lifecycle_002_resolving_twice_is_a_no_op(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-open"),
            action_description="send Sam the book",
        )
        fingerprint = commitment_fingerprint(
            source=source,
            counterparty_entity_id=entity_id,
            action_description="send Sam the book",
        )
        receipt = {"source": "owner_confirmed", "session_id": "session-close"}

        assert (
            await resolve_commitment(
                pool,
                source=source,
                fingerprint=fingerprint,
                resolution_reason="satisfied",
                evidence_closed=receipt,
            )
            is not None
        )
        assert (
            await resolve_commitment(
                pool,
                source=source,
                fingerprint=fingerprint,
                resolution_reason="cancelled",
                evidence_closed=receipt,
            )
            is None
        )

        rows = await _rows_for(pool, source)
        assert len(rows) == 1
        assert _metadata(rows[0])["resolution_reason"] == "satisfied"


class TestCommitmentQuerySurface:
    async def test_req_commitment_lifecycle_002_active_list_excludes_non_commitments(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        """The same producer's ordinary owner conditions must not leak into the panel."""
        await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        await reconcile_snapshot(
            pool,
            source=source,
            observations=[
                Observation(
                    fingerprint="not-a-commitment",
                    summary="Sam's birthday is approaching",
                    metadata={"class": "reminder"},
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=3600,
        )

        active = await list_active_commitments(pool, source=source)
        assert [row["summary"] for row in active] == ["Send Sam the book"]
        assert len(await _rows_for(pool, source)) == 2

    async def test_req_commitment_lifecycle_002_active_list_excludes_resolved_commitments(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        await resolve_commitment(
            pool,
            source=source,
            fingerprint=commitment_fingerprint(
                source=source,
                counterparty_entity_id=entity_id,
                action_description="send Sam the book",
            ),
            resolution_reason="satisfied",
            evidence_closed={"source": "owner_confirmed"},
        )

        assert await list_active_commitments(pool, source=source) == []

    async def test_req_commitment_lifecycle_001_entity_list_spans_every_producer(
        self, pool: asyncpg.Pool, entity_id: str
    ) -> None:
        """Outstanding-with-Sam is not any single butler's question."""
        relationship = f"relationship:commitment-{uuid.uuid4().hex[:12]}"
        finance = f"finance:obligation-{uuid.uuid4().hex[:12]}"
        other_entity = str(uuid.uuid4())

        await create_commitment(
            pool,
            source=relationship,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        await create_commitment(
            pool,
            source=finance,
            summary="Repay Sam for the concert ticket",
            kind="obligation",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.85,
            evidence_opened=_evidence("session-2"),
            action_description="repay Sam for the concert ticket",
        )
        await create_commitment(
            pool,
            source=relationship,
            summary="Send Alex the recipe",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=other_entity,
            confidence=0.9,
            evidence_opened=_evidence("session-3"),
            action_description="send Alex the recipe",
        )

        rows = await list_entity_commitments(pool, entity_id=entity_id)
        assert {row["source"] for row in rows} == {relationship, finance}
        assert all(row["metadata"]["counterparty_entity_id"] == entity_id for row in rows)
        assert len(rows) == 2

    async def test_req_commitment_lifecycle_001_entity_list_can_include_resolved_history(
        self, pool: asyncpg.Pool, source: str, entity_id: str
    ) -> None:
        await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=entity_id,
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        await resolve_commitment(
            pool,
            source=source,
            fingerprint=commitment_fingerprint(
                source=source,
                counterparty_entity_id=entity_id,
                action_description="send Sam the book",
            ),
            resolution_reason="satisfied",
            evidence_closed={"source": "owner_confirmed"},
        )

        assert await list_entity_commitments(pool, entity_id=entity_id) == []
        history = await list_entity_commitments(pool, entity_id=entity_id, include_resolved=True)
        assert [row["state"] for row in history] == ["resolved"]
