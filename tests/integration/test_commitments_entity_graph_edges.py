"""Real-Postgres regression: commitment write-behind onto entity_graph_edges.

RFC 0031 (about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md),
bu-8cdl1.8 Slice 2 — a directed commitment (``owner_to_other``/
``other_to_owner``) with a counterparty is an entity-to-entity relationship,
projected onto ``public.entity_graph_edges`` in the same transaction as the
``public.owner_conditions`` write via ``reconcile_snapshot``'s ``post_write``
hook (see ``butlers.core.commitments._project_commitment_edge``).

This file provisions a fresh migrated DB *per test* rather than sharing
``tests/integration/test_commitments_roundtrip.py``'s module-scoped one (or
even sharing one across the tests in this file): the production model assumes
exactly one owner entity fleet-wide, so ``_project_commitment_edge`` and
``backfill_commitment_edges`` both pick "the" owner with no per-test
discriminator, and ``backfill_commitment_edges`` scans every commitment-class
row in the DB with no ``source`` filter. A prior test's commitment row whose
counterparty entity got cleaned up would otherwise still be a backfill
candidate and trip an FK violation on the next test's re-run. A fresh DB per
test costs a few extra seconds against a single migration chain ("core") but
sidesteps that whole class of cross-test contamination.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.core.commitments import create_commitment
from butlers.core.entity_graph_edges import backfill_commitment_edges
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
def migrated_db_url(postgres_container) -> str:
    """A fresh DB per test — see the module docstring for why this is not module-scoped."""
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


def _evidence(session: str) -> dict[str, str]:
    return {
        "source": "conversation",
        "session_id": session,
        "excerpt": "synthetic test utterance",
    }


async def _rows_for(pool: asyncpg.Pool, source: str) -> list[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id FROM public.owner_conditions WHERE source = $1",
        source,
    )


async def _edge_for_condition(pool: asyncpg.Pool, condition_id: uuid.UUID) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT subject_entity_id, predicate, object_entity_id FROM public.entity_graph_edges"
        " WHERE source_schema = 'public' AND source_table = 'owner_conditions'"
        " AND source_id = $1",
        condition_id,
    )


@pytest.fixture
async def owner_and_counterparty(pool: asyncpg.Pool) -> tuple[uuid.UUID, uuid.UUID]:
    """A real owner-role entity plus a counterparty in this test's fresh DB."""
    owner_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', '{owner}') RETURNING id"
    )
    counterparty_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Sam', 'person', '{}') RETURNING id"
    )
    return owner_id, counterparty_id


class TestCommitmentEntityGraphProjection:
    async def test_owner_to_other_projects_owner_as_subject(
        self,
        pool: asyncpg.Pool,
        source: str,
        owner_and_counterparty: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        owner_id, counterparty_id = owner_and_counterparty
        transition = await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=str(counterparty_id),
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        edge = await _edge_for_condition(pool, transition.condition_id)
        assert edge is not None
        assert edge["subject_entity_id"] == owner_id
        assert edge["predicate"] == "committed-to"
        assert edge["object_entity_id"] == counterparty_id

    async def test_other_to_owner_swaps_subject_and_object(
        self,
        pool: asyncpg.Pool,
        source: str,
        owner_and_counterparty: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        owner_id, counterparty_id = owner_and_counterparty
        transition = await create_commitment(
            pool,
            source=source,
            summary="Sam owes the owner a book",
            kind="waiting_for",
            direction="other_to_owner",
            counterparty_entity_id=str(counterparty_id),
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="Sam sends the owner a book",
        )
        edge = await _edge_for_condition(pool, transition.condition_id)
        assert edge is not None
        assert edge["subject_entity_id"] == counterparty_id
        assert edge["object_entity_id"] == owner_id

    async def test_self_direction_projects_no_edge(
        self,
        pool: asyncpg.Pool,
        source: str,
        owner_and_counterparty: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        transition = await create_commitment(
            pool,
            source=source,
            summary="Finish the report",
            kind="obligation",
            direction="self",
            counterparty_entity_id=None,
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="finish the quarterly report",
        )
        assert await _edge_for_condition(pool, transition.condition_id) is None

    async def test_no_owner_entity_projects_no_edge(self, pool: asyncpg.Pool, source: str) -> None:
        """No owner entity exists yet -- a no-op, not a failure."""
        counterparty_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type, roles) "
            "VALUES ('Sam', 'person', '{}') RETURNING id"
        )
        transition = await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=str(counterparty_id),
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        assert await _edge_for_condition(pool, transition.condition_id) is None

    async def test_projection_failure_rolls_back_the_commitment_write(
        self,
        pool: asyncpg.Pool,
        source: str,
        owner_and_counterparty: tuple[uuid.UUID, uuid.UUID],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RFC 0031 write-behind contract: a projection failure fails the whole write."""
        from butlers.core import entity_graph_edges

        _, counterparty_id = owner_and_counterparty

        async def _boom(*args, **kwargs):
            raise RuntimeError("simulated entity_graph_edges projection failure")

        monkeypatch.setattr(entity_graph_edges, "project_entity_graph_edge", _boom)

        with pytest.raises(RuntimeError, match="simulated entity_graph_edges projection failure"):
            await create_commitment(
                pool,
                source=source,
                summary="Send Sam the book",
                kind="promise",
                direction="owner_to_other",
                counterparty_entity_id=str(counterparty_id),
                confidence=0.9,
                evidence_opened=_evidence("session-1"),
                action_description="send Sam the book",
            )

        assert await _rows_for(pool, source) == []

    async def test_backfill_is_idempotent(
        self,
        pool: asyncpg.Pool,
        source: str,
        owner_and_counterparty: tuple[uuid.UUID, uuid.UUID],
    ) -> None:
        owner_id, counterparty_id = owner_and_counterparty
        transition = await create_commitment(
            pool,
            source=source,
            summary="Send Sam the book",
            kind="promise",
            direction="owner_to_other",
            counterparty_entity_id=str(counterparty_id),
            confidence=0.9,
            evidence_opened=_evidence("session-1"),
            action_description="send Sam the book",
        )
        # Simulate a historical row written before this projection existed --
        # remove the edge the live writer just projected.
        await pool.execute(
            "DELETE FROM public.entity_graph_edges"
            " WHERE source_schema = 'public' AND source_table = 'owner_conditions'"
            " AND source_id = $1",
            transition.condition_id,
        )

        first_count = await backfill_commitment_edges(pool)
        second_count = await backfill_commitment_edges(pool)

        edge = await _edge_for_condition(pool, transition.condition_id)
        assert first_count == 1
        assert second_count == 0
        assert edge is not None
        assert edge["subject_entity_id"] == owner_id
        assert edge["object_entity_id"] == counterparty_id
