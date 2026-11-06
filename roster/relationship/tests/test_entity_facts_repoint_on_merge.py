"""Tests for relationship.entity_facts re-pointing during contact_merge.

Covers bu-9z7nd (subject-side) and bu-igcxb (object-side) on the real core,
memory and relationship chains: facts are seeded rows and every assertion reads
the resulting row state.

contact_merge() must re-point ALL active triples in relationship.entity_facts:
- SUBJECT side (bu-9z7nd): rows where subject = source entity_id → subject = target entity_id.
- OBJECT side (bu-igcxb): rows where object = source entity_id::text AND object_kind = 'entity'
  → object = target entity_id::text. These are relational predicates ('knows', 'family-of')
  where the merged-away entity appears as the object.

The merge authority then tombstones the source in the same transaction
(bu-dhloi1.40, see test_contact_merge_atomicity.py).

Test scope (subject-side):
  (a) Source triples with no conflict → re-pointed to target subject (several at once).
  (b) Source triple conflicts with target (target wins) → source superseded.
  (c) Source triple conflicts with target (source wins) → conflict superseded,
      source re-pointed.
  (d) Idempotency: a second re-point pass finds no active source-subject triples
      and changes nothing.
  (e) A database error in the re-point propagates (the single merge transaction
      rolls back) and the merge authority is never reached.
  (f) Source entity_id is NULL → entity_facts blocks (subject and object side)
      are skipped entirely.

Test scope (object-side, bu-igcxb):
  (g) Basic object-side re-point: row with object = src entity → object = tgt entity.
  (h) Object-side conflict (target wins) → source object-row superseded.
  (i) Object-side conflict (source wins) → conflict superseded, source re-pointed.
  (j) object_kind='literal' rows are NOT re-pointed (only 'entity' rows are touched).
  (l) Idempotency: a second object-side pass finds no source rows → no-op.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import row
from butlers.tools.relationship.contacts import contact_merge

_db_backed = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_contacts = sys.modules[contact_merge.__module__]


# ---------------------------------------------------------------------------
# Real-DB seeding
# ---------------------------------------------------------------------------


@pytest.fixture
async def rel_pool(db):
    """Core + memory + relationship chains (flat; entity_facts in ``relationship``)."""
    return await db("core", "memory", "relationship", min_size=2, max_size=8)


async def _entity(pool, name: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) VALUES ($1, 'person') "
        "RETURNING id",
        name,
    )


async def _contact(pool, name: str, entity_id: uuid.UUID) -> uuid.UUID:
    contact_id = await pool.fetchval(
        "INSERT INTO contacts (name, entity_id) VALUES ($1, $2) RETURNING id", name, entity_id
    )
    await pool.execute(
        "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
        contact_id,
        entity_id,
    )
    return contact_id


async def _merge_pair(pool) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """(source contact, target contact, source entity, target entity)."""
    src_entity = await _entity(pool, "Alice (duplicate)")
    tgt_entity = await _entity(pool, "Alice (canonical)")
    src_contact = await _contact(pool, "Alice (duplicate)", src_entity)
    tgt_contact = await _contact(pool, "Alice (canonical)", tgt_entity)
    return src_contact, tgt_contact, src_entity, tgt_entity


async def _fact(
    pool,
    subject: uuid.UUID,
    predicate: str,
    obj: str,
    *,
    kind: str = "literal",
    conf: float = 1.0,
) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src, "
        "conf) VALUES ($1, $2, $3, $4, 'test', $5) RETURNING id",
        subject,
        predicate,
        obj,
        kind,
        conf,
    )


async def _facts(pool) -> dict[uuid.UUID, tuple]:
    """Every fact as ``id -> (subject, object, validity)``."""
    rows = await pool.fetch("SELECT id, subject, object, validity FROM relationship.entity_facts")
    return {r["id"]: (r["subject"], r["object"], r["validity"]) for r in rows}


async def _second_repoint_pass(pool, src_entity: uuid.UUID, tgt_entity: uuid.UUID) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await _contacts._repoint_entity_facts(conn, src_entity, tgt_entity)


def _patch_entity_merge():
    # The authority is imported inside the function body so we must patch it
    # at the source module where it is defined.
    return patch(
        "butlers.tools.relationship.entity_merge.merge_entity_pair_in_transaction",
        new_callable=AsyncMock,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntityFactsRepointOnMerge:
    """entity_facts rows with subject=source are re-pointed to subject=target."""

    pytestmark = _db_backed

    async def test_subject_side_repoints_and_resolves_conflicts(self, rel_pool):
        """(a)-(d): plain triples move, SPO collisions keep the higher conf, rerun is a no-op."""
        src_contact, tgt_contact, src, tgt = await _merge_pair(rel_pool)
        # (a) several source triples with no target counterpart.
        plain = [
            await _fact(rel_pool, src, "has-email", "a@eg.com"),
            await _fact(rel_pool, src, "has-phone", "+15550001"),
            await _fact(rel_pool, src, "has-telegram", "12345"),
        ]
        # (b) target has the same triple with higher confidence: target wins.
        losing_src = await _fact(rel_pool, src, "has-email", "both@eg.com", conf=0.5)
        winning_tgt = await _fact(rel_pool, tgt, "has-email", "both@eg.com", conf=0.9)
        # (c) source has the same triple with higher confidence: source wins.
        winning_src = await _fact(rel_pool, src, "has-phone", "+15559999", conf=0.95)
        losing_tgt = await _fact(rel_pool, tgt, "has-phone", "+15559999", conf=0.5)

        result = await contact_merge(rel_pool, src_contact, tgt_contact)

        assert result["id"] == tgt_contact
        facts = await _facts(rel_pool)
        for fact_id in plain:
            assert facts[fact_id][0] == tgt
            assert facts[fact_id][2] == "active"
        assert facts[losing_src] == (src, "both@eg.com", "superseded")
        assert facts[winning_tgt] == (tgt, "both@eg.com", "active")
        assert facts[losing_tgt] == (tgt, "+15559999", "superseded")
        assert facts[winning_src] == (tgt, "+15559999", "active")
        active_on_source = [f for f in facts.values() if f[0] == src and f[2] == "active"]
        assert active_on_source == []

        # (d) A second pass has nothing left to move.
        await _second_repoint_pass(rel_pool, src, tgt)
        assert await _facts(rel_pool) == facts

    async def test_entity_facts_error_propagates_and_skips_entity_merge(
        self, rel_pool, monkeypatch
    ):
        """(e) A DB error during the entity_facts re-point is no longer swallowed after a
        partial commit (bu-p2bjsf): it propagates out of the single merge transaction
        (rolling it back) and the merge authority is never reached."""
        src_contact, tgt_contact, src, _tgt = await _merge_pair(rel_pool)
        fact_id = await _fact(rel_pool, src, "has-email", "a@eg.com")
        monkeypatch.setattr(
            _contacts,
            "_repoint_entity_facts",
            AsyncMock(side_effect=asyncpg.UndefinedTableError("relation does not exist")),
        )

        with (
            _patch_entity_merge() as mock_em,
            pytest.raises(asyncpg.UndefinedTableError),
        ):
            await contact_merge(rel_pool, src_contact, tgt_contact)

        mock_em.assert_not_awaited()
        # The whole merge rolled back: the source bridge row and its fact are intact.
        assert (
            await rel_pool.fetchval(
                "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1", src_contact
            )
            == src
        )
        assert (await _facts(rel_pool))[fact_id] == (src, "a@eg.com", "active")


# ---------------------------------------------------------------------------
# Object-side re-pointing tests (bu-igcxb)
# ---------------------------------------------------------------------------


class TestObjectSideEntityFactsRepointOnMerge:
    """entity_facts rows with object=source entity_id (object_kind='entity') are re-pointed."""

    pytestmark = _db_backed

    async def test_object_side_repoints_and_resolves_conflicts(self, rel_pool):
        """(g)-(j), (l): entity objects move, collisions keep the higher conf, literals stay."""
        src_contact, tgt_contact, src, tgt = await _merge_pair(rel_pool)
        src_obj, tgt_obj = str(src), str(tgt)
        knower = await _entity(rel_pool, "Bob")
        relative = await _entity(rel_pool, "Carol")
        friend = await _entity(rel_pool, "Dan")
        noter = await _entity(rel_pool, "Erin")
        # (g) no conflict.
        plain = await _fact(rel_pool, knower, "knows", src_obj, kind="entity")
        # (h) target wins.
        losing_src = await _fact(rel_pool, relative, "family-of", src_obj, kind="entity", conf=0.4)
        winning_tgt = await _fact(rel_pool, relative, "family-of", tgt_obj, kind="entity", conf=0.9)
        # (i) source wins.
        winning_src = await _fact(rel_pool, friend, "knows", src_obj, kind="entity", conf=0.95)
        losing_tgt = await _fact(rel_pool, friend, "knows", tgt_obj, kind="entity", conf=0.3)
        # (j) a literal whose text happens to equal the source id is not an entity edge.
        literal = await _fact(rel_pool, noter, "has-note", src_obj, kind="literal")

        result = await contact_merge(rel_pool, src_contact, tgt_contact)

        assert result["id"] == tgt_contact
        facts = await _facts(rel_pool)
        assert facts[plain] == (knower, tgt_obj, "active")
        assert facts[losing_src] == (relative, src_obj, "superseded")
        assert facts[winning_tgt] == (relative, tgt_obj, "active")
        assert facts[losing_tgt] == (friend, tgt_obj, "superseded")
        assert facts[winning_src] == (friend, tgt_obj, "active")
        assert facts[literal] == (noter, src_obj, "active")

        # (l) A second pass has nothing left to move.
        await _second_repoint_pass(rel_pool, src, tgt)
        assert await _facts(rel_pool) == facts


@pytest.mark.unit
async def test_null_source_entity_skips_entity_facts_blocks():
    """(f) Source contact has NULL entity_id: neither entity_facts block runs.

    ``_CONTACT_MERGE_SELECT`` joins the entity, so this is a defensive branch a
    real row cannot reach; the merge is driven from configured rows instead.
    """
    contact = {"canonical_name": "Alice Smith", "entity_metadata": None}
    source_id, target_id = uuid.uuid4(), uuid.uuid4()
    source = row(id=source_id, entity_id=None, **contact)
    target = row(id=target_id, entity_id=uuid.uuid4(), **contact)
    pool = fake_pool(fetchrow=AsyncMock(side_effect=[source, target, target]), execute=None)

    with _patch_entity_merge() as mock_em:
        result = await contact_merge(pool, source_id, target_id)

    mock_em.assert_not_awaited()
    statements = [call.args[0] for call in pool.execute.await_args_list]
    assert statements, "the child-table re-point still runs"
    assert not [sql for sql in statements if "entity_facts" in sql]
    pool.fetch.assert_not_awaited()
    assert result["id"] == target_id
