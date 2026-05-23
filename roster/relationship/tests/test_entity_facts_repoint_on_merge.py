"""Tests for relationship.entity_facts re-pointing during contact_merge (bu-9z7nd).

contact_merge() must re-point ALL active triples in relationship.entity_facts
where subject = source entity_id to subject = target entity_id.  This fills the
gap identified in bu-9z7nd: entity_merge re-points memory.facts but cannot
reach relationship.entity_facts (different column layout: subject vs entity_id).

Test scope:
  (a) Source triples with no conflict → re-pointed to target subject.
  (b) Source triple conflicts with target (target wins) → source superseded.
  (c) Source triple conflicts with target (source wins) → conflict superseded,
      source re-pointed.
  (d) Idempotency: merge called twice → second call is a no-op (no active
      source-subject triples remain after the first pass).
  (e) Table absent (UndefinedTableError or similar) → warning swallowed; merge
      still returns target contact dict.
  (f) Source entity_id is NULL → entity_facts block is skipped entirely.

All tests are pure unit tests (no Docker / Postgres required).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Shared UUIDs
# ---------------------------------------------------------------------------

_SOURCE_ID = uuid.uuid4()
_TARGET_ID = uuid.uuid4()
_SRC_ENTITY_ID = uuid.uuid4()
_TGT_ENTITY_ID = uuid.uuid4()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COLS = {
    "id",
    "entity_id",
    "first_name",
    "last_name",
    "nickname",
    "company",
    "job_title",
    "gender",
    "pronouns",
    "avatar_url",
    "listed",
    "archived_at",
    "metadata",
    "details",
    "name",
    "updated_at",
}


class _AsyncCM:
    """Minimal async context manager (for pool.acquire() / conn.transaction())."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _make_contact_row(
    contact_id: uuid.UUID,
    entity_id: uuid.UUID | None,
) -> dict:
    return {
        "id": contact_id,
        "entity_id": entity_id,
        "first_name": "Alice",
        "last_name": "Smith",
        "nickname": None,
        "company": None,
        "job_title": None,
        "gender": None,
        "pronouns": None,
        "avatar_url": None,
        "listed": True,
        "archived_at": None,
        "metadata": {},
        "details": {},
        "name": "Alice Smith",
        "updated_at": None,
    }


def _make_ef_row(
    row_id: uuid.UUID,
    subject: uuid.UUID,
    predicate: str = "has-email",
    object_val: str = "src@example.com",
    conf: float = 1.0,
) -> dict:
    """Minimal relationship.entity_facts row dict."""
    return {
        "id": row_id,
        "subject": subject,
        "predicate": predicate,
        "object": object_val,
        "conf": conf,
    }


def _make_pool(
    source_row: dict,
    target_row: dict,
    *,
    ef_rows_on_conn: list[dict] | None = None,
    conn_fetchrow_side_effect: list[Any] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a pool mock wired for contact_merge.

    contact_merge calls on pool (outside transaction):
      1. pool.fetchrow(source contact)
      2. pool.fetchrow(target contact)
      3. pool.fetch(source contact_info pre-fetch)
      4. pool.acquire() → conn for the _child_tables transaction
      5. pool.acquire() → conn for the entity_facts re-point transaction
      6. pool.fetchrow(final target fetch)

    Returns (pool, conn) so tests can assert on conn.execute / conn.fetch.
    """
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[source_row, target_row, target_row])
    pool.fetch = AsyncMock(return_value=[])  # no contact_info rows

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    # conn.fetch returns entity_facts rows for the SELECT inside the ef block
    conn.fetch = AsyncMock(return_value=ef_rows_on_conn or [])
    # conn.fetchrow returns conflict-check results; callers override as needed
    if conn_fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=conn_fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=None)  # default: no conflict

    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


def _patch_table_columns(cols: set[str] = _COLS):
    return patch(
        "butlers.tools.relationship.contacts.table_columns",
        new=AsyncMock(return_value=cols),
    )


def _patch_entity_merge():
    # entity_merge is imported inside the function body so we must patch it
    # at the source module where it is defined.
    return patch(
        "butlers.modules.memory.tools.entities.entity_merge",
        new_callable=AsyncMock,
    )


def _patch_retract_all():
    return patch(
        "butlers.tools.relationship.contacts.retract_all_contact_info_facts",
        new_callable=AsyncMock,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntityFactsRepointOnMerge:
    """entity_facts rows with subject=source are re-pointed to subject=target."""

    async def test_no_conflict_repoints_subject(self):
        """(a) Source triple has no matching target triple — UPDATE subject to target."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, _SRC_ENTITY_ID)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)
        ef_id = uuid.uuid4()
        ef_rows = [
            _make_ef_row(ef_id, _SRC_ENTITY_ID, predicate="has-email", object_val="s@eg.com")
        ]

        pool, conn = _make_pool(
            src_row,
            tgt_row,
            ef_rows_on_conn=ef_rows,
            conn_fetchrow_side_effect=[
                None,  # no conflict for the one ef row
            ],
        )

        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        # Should have called UPDATE ... SET subject = target WHERE id = ef_id
        update_calls = [str(c) for c in conn.execute.call_args_list]
        repoint_calls = [c for c in update_calls if "SET subject" in c]
        assert len(repoint_calls) == 1
        assert str(_TGT_ENTITY_ID) in repoint_calls[0]
        assert str(ef_id) in repoint_calls[0]
        assert result["id"] == _TARGET_ID

    async def test_conflict_target_wins_supersedes_source(self):
        """(b) Target triple exists with higher or equal conf — source row superseded."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, _SRC_ENTITY_ID)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)
        ef_id = uuid.uuid4()
        conflict_id = uuid.uuid4()

        ef_rows = [_make_ef_row(ef_id, _SRC_ENTITY_ID, conf=0.5)]
        # target has higher confidence
        conflict_row = {"id": conflict_id, "conf": 0.9}

        pool, conn = _make_pool(
            src_row,
            tgt_row,
            ef_rows_on_conn=ef_rows,
            conn_fetchrow_side_effect=[conflict_row],
        )

        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        # Only the source row should be superseded; no subject re-point should occur
        update_calls = [str(c) for c in conn.execute.call_args_list]
        supersede_calls = [c for c in update_calls if "superseded" in c]
        repoint_calls = [c for c in update_calls if "SET subject" in c]
        assert len(supersede_calls) == 1
        assert str(ef_id) in supersede_calls[0]
        assert len(repoint_calls) == 0

    async def test_conflict_source_wins_supersedes_conflict_and_repoints(self):
        """(c) Source triple has higher conf — conflict superseded; source re-pointed."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, _SRC_ENTITY_ID)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)
        ef_id = uuid.uuid4()
        conflict_id = uuid.uuid4()

        ef_rows = [_make_ef_row(ef_id, _SRC_ENTITY_ID, conf=0.95)]
        # target has lower confidence
        conflict_row = {"id": conflict_id, "conf": 0.5}

        pool, conn = _make_pool(
            src_row,
            tgt_row,
            ef_rows_on_conn=ef_rows,
            conn_fetchrow_side_effect=[conflict_row],
        )

        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        update_calls = [str(c) for c in conn.execute.call_args_list]
        supersede_calls = [c for c in update_calls if "superseded" in c]
        repoint_calls = [c for c in update_calls if "SET subject" in c]
        # Conflict (target) should be superseded
        assert len(supersede_calls) == 1
        assert str(conflict_id) in supersede_calls[0]
        # Source should be re-pointed to target
        assert len(repoint_calls) == 1
        assert str(_TGT_ENTITY_ID) in repoint_calls[0]
        assert str(ef_id) in repoint_calls[0]

    async def test_idempotency_second_call_is_no_op(self):
        """(d) After merge, no source-subject triples remain — second call does nothing."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, _SRC_ENTITY_ID)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)

        # First call: one source triple, no conflict
        ef_id = uuid.uuid4()
        ef_rows_first = [_make_ef_row(ef_id, _SRC_ENTITY_ID)]
        pool1, conn1 = _make_pool(
            src_row,
            tgt_row,
            ef_rows_on_conn=ef_rows_first,
            conn_fetchrow_side_effect=[None],
        )
        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            await contact_merge(pool1, _SOURCE_ID, _TARGET_ID)

        # Second call: no source triples remain (already re-pointed)
        pool2, conn2 = _make_pool(
            src_row,
            tgt_row,
            ef_rows_on_conn=[],  # empty — all triples already re-pointed
        )
        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            result = await contact_merge(pool2, _SOURCE_ID, _TARGET_ID)

        # Second call: no UPDATE statements on entity_facts at all
        update_calls = [str(c) for c in conn2.execute.call_args_list]
        ef_updates = [c for c in update_calls if "entity_facts" in c]
        assert len(ef_updates) == 0
        assert result["id"] == _TARGET_ID

    async def test_entity_facts_error_swallowed_merge_succeeds(self):
        """(e) DB error during entity_facts re-point is swallowed; merge still returns target."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, _SRC_ENTITY_ID)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)

        pool, conn = _make_pool(src_row, tgt_row)
        # Force the entity_facts SELECT to fail (simulate table absent or DB error).
        # Must be asyncpg.PostgresError (or subclass) — the guard only swallows
        # Postgres-level failures, not all exceptions.
        conn.fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError("relation does not exist"))

        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        # Merge must complete and return target contact dict regardless
        assert result["id"] == _TARGET_ID

    async def test_null_source_entity_skips_entity_facts_block(self):
        """(f) Source contact has NULL entity_id — entity_facts block is skipped entirely."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, entity_id=None)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)

        pool, conn = _make_pool(src_row, tgt_row)

        with _patch_table_columns(), _patch_entity_merge() as mock_em, _patch_retract_all():
            result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        # entity_merge must not be called when source entity_id is NULL
        mock_em.assert_not_awaited()
        # No entity_facts UPDATE calls
        update_calls = [str(c) for c in conn.execute.call_args_list]
        ef_updates = [c for c in update_calls if "entity_facts" in c]
        assert len(ef_updates) == 0
        assert result["id"] == _TARGET_ID

    async def test_multiple_source_triples_all_repointed(self):
        """All source-subject triples (multiple) are processed and re-pointed."""
        from butlers.tools.relationship.contacts import contact_merge

        src_row = _make_contact_row(_SOURCE_ID, _SRC_ENTITY_ID)
        tgt_row = _make_contact_row(_TARGET_ID, _TGT_ENTITY_ID)

        ef_id1 = uuid.uuid4()
        ef_id2 = uuid.uuid4()
        ef_id3 = uuid.uuid4()
        ef_rows = [
            _make_ef_row(ef_id1, _SRC_ENTITY_ID, predicate="has-email", object_val="a@eg.com"),
            _make_ef_row(ef_id2, _SRC_ENTITY_ID, predicate="has-phone", object_val="+15550001"),
            _make_ef_row(ef_id3, _SRC_ENTITY_ID, predicate="has-telegram", object_val="12345"),
        ]

        pool, conn = _make_pool(
            src_row,
            tgt_row,
            ef_rows_on_conn=ef_rows,
            # All three rows have no conflict
            conn_fetchrow_side_effect=[None, None, None],
        )

        with _patch_table_columns(), _patch_entity_merge(), _patch_retract_all():
            result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        update_calls = [str(c) for c in conn.execute.call_args_list]
        repoint_calls = [c for c in update_calls if "SET subject" in c]
        assert len(repoint_calls) == 3
        repointed_ids = " ".join(repoint_calls)
        assert str(ef_id1) in repointed_ids
        assert str(ef_id2) in repointed_ids
        assert str(ef_id3) in repointed_ids
        assert result["id"] == _TARGET_ID
