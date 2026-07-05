"""Unit tests for butlers.core.delegation_ledger (bu-gxmfx).

Covers the ledger writer/reader and the catalog-attribution routing
resolution in isolation, mirroring the AsyncMock-pool style used by
``tests/core/test_attention_ledger.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.core import delegation_ledger
from butlers.core.delegation_ledger import (
    VALID_STATUSES,
    get_delegation,
    list_delegations,
    mark_dispatch_outcome,
    record_answer,
    record_ask,
    resolve_target_via_catalog,
)

pytestmark = pytest.mark.unit


class TestRecordAsk:
    async def test_rejects_invalid_status(self):
        pool = AsyncMock()
        with pytest.raises(ValueError):
            await record_ask(
                pool,
                asking_butler="finance",
                question="What's the current Dunbar tier for Alice?",
                status="answered",
            )
        pool.fetchval.assert_not_awaited()

    async def test_pending_insert_returns_row_id(self):
        row_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=row_id)

        result = await record_ask(
            pool,
            asking_butler="finance",
            question="Who is Alice's employer?",
            status="pending",
            target_butler="relationship",
            catalog_match_id=uuid.uuid4(),
            catalog_score=0.83,
        )
        assert result == str(row_id)

        pool.fetchval.assert_awaited_once()
        query, *params = pool.fetchval.await_args.args
        assert "INSERT INTO public.delegation_ledger" in query
        assert params[0] == "finance"
        assert params[1] == "Who is Alice's employer?"
        assert params[2] == "relationship"
        assert params[5] == "pending"

    async def test_unroutable_insert_allows_null_target(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=uuid.uuid4())

        await record_ask(
            pool,
            asking_butler="finance",
            question="asdf gibberish",
            status="unroutable",
            reason="no_catalog_match",
        )
        _query, *params = pool.fetchval.await_args.args
        assert params[2] is None  # target_butler
        assert params[5] == "unroutable"
        assert params[6] == "no_catalog_match"

    async def test_db_error_propagates(self):
        """Unlike attention_ledger, delegation_ledger IS the delegation record --
        a write failure must surface to the caller, never be swallowed."""
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=Exception("connection reset"))

        with pytest.raises(Exception, match="connection reset"):
            await record_ask(
                pool,
                asking_butler="finance",
                question="q",
                status="pending",
                target_butler="relationship",
            )


class TestMarkDispatchOutcome:
    async def test_rejects_invalid_status(self):
        pool = AsyncMock()
        with pytest.raises(ValueError):
            await mark_dispatch_outcome(pool, uuid.uuid4(), status="pending")
        pool.execute.assert_not_awaited()

    async def test_routed_update_scoped_to_pending_rows(self):
        pool = AsyncMock()
        ledger_id = uuid.uuid4()
        await mark_dispatch_outcome(pool, ledger_id, status="routed")

        pool.execute.assert_awaited_once()
        query, row_id, status, reason = pool.execute.await_args.args
        assert "UPDATE public.delegation_ledger" in query
        assert "WHERE id = $1 AND status = 'pending'" in query
        assert row_id == ledger_id
        assert status == "routed"
        assert reason is None

    async def test_failed_update_propagates_db_errors(self):
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(Exception, match="boom"):
            await mark_dispatch_outcome(pool, uuid.uuid4(), status="failed", reason="unreachable")


class TestRecordAnswer:
    async def test_successful_answer_returns_row_dict(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            return_value={
                "id": uuid.uuid4(),
                "asking_butler": "finance",
                "question": "q",
                "target_butler": "relationship",
                "catalog_match_id": None,
                "catalog_score": None,
                "status": "answered",
                "reason": None,
                "answer": "Alice's employer is Acme Corp.",
                "answered_at": None,
                "answering_butler": "relationship",
                "asked_at": None,
                "metadata": None,
            }
        )
        result = await record_answer(
            pool, uuid.uuid4(), answering_butler="relationship", answer="Acme Corp."
        )
        assert result is not None
        assert result["answering_butler"] == "relationship"
        pool.fetchrow.assert_awaited_once()
        query = pool.fetchrow.await_args.args[0]
        assert "status = 'routed'" in query
        assert "target_butler = $2" in query

    async def test_guard_failure_returns_none(self):
        """No row matched (wrong butler, wrong status, or unknown id) -- an
        honest None, never a fabricated success."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        result = await record_answer(pool, uuid.uuid4(), answering_butler="finance", answer="nope")
        assert result is None


class TestGetAndListDelegations:
    async def test_get_delegation_returns_none_when_absent(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        assert await get_delegation(pool, uuid.uuid4()) is None

    async def test_get_delegation_returns_dict(self):
        pool = AsyncMock()
        row_id = uuid.uuid4()
        pool.fetchrow = AsyncMock(return_value={"id": row_id, "status": "routed"})
        result = await get_delegation(pool, row_id)
        assert result == {"id": row_id, "status": "routed"}

    async def test_list_delegations_applies_filters_and_pagination(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=2)
        pool.fetch = AsyncMock(return_value=[{"id": uuid.uuid4(), "status": "answered"}])

        total, rows = await list_delegations(
            pool,
            status="answered",
            asking_butler="finance",
            target_butler="relationship",
            offset=10,
            limit=5,
        )
        assert total == 2
        assert len(rows) == 1

        fetch_query, *fetch_args = pool.fetch.await_args.args
        assert "status = $1" in fetch_query
        assert "asking_butler = $2" in fetch_query
        assert "target_butler = $3" in fetch_query
        # offset/limit are the last two positional args.
        assert fetch_args[-2:] == [10, 5]

    async def test_list_delegations_no_filters_no_where_clause(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = AsyncMock(return_value=[])

        await list_delegations(pool)
        fetch_query = pool.fetch.await_args.args[0]
        assert "WHERE" not in fetch_query

    async def test_valid_statuses_matches_migration_check_constraint(self):
        assert VALID_STATUSES == {"pending", "routed", "unroutable", "failed", "answered"}


class TestResolveTargetViaCatalog:
    """Patches ``core.delegation_ledger.search_memory_catalog`` -- the
    dependency-inversion hook stub bound into this module's namespace at
    import time (see ``butlers.core.memory_hooks``) -- rather than reaching
    into ``butlers.modules.memory`` directly, which core must not import.
    """

    async def test_no_hits_returns_all_none(self, monkeypatch):
        async def _fake_search(pool, query, *, limit, mode):
            return []

        monkeypatch.setattr(delegation_ledger, "search_memory_catalog", _fake_search)
        target, match_id, score = await resolve_target_via_catalog(AsyncMock(), "question")
        assert (target, match_id, score) == (None, None, None)

    async def test_top_hit_source_butler_wins(self, monkeypatch):
        hit_id = uuid.uuid4()

        async def _fake_search(pool, query, *, limit, mode):
            return [
                {
                    "id": hit_id,
                    "source_butler": "relationship",
                    "source_schema": "relationship",
                    "rrf_score": 0.05,
                }
            ]

        monkeypatch.setattr(delegation_ledger, "search_memory_catalog", _fake_search)
        target, match_id, score = await resolve_target_via_catalog(
            AsyncMock(), "Who is Alice's employer?"
        )
        assert target == "relationship"
        assert match_id == str(hit_id)
        assert score == 0.05

    async def test_falls_back_to_source_schema_when_source_butler_absent(self, monkeypatch):
        hit_id = uuid.uuid4()

        async def _fake_search(pool, query, *, limit, mode):
            return [{"id": hit_id, "source_butler": None, "source_schema": "finance"}]

        monkeypatch.setattr(delegation_ledger, "search_memory_catalog", _fake_search)
        target, _match_id, _score = await resolve_target_via_catalog(AsyncMock(), "question")
        assert target == "finance"

    async def test_search_failure_fails_closed_to_unroutable(self, monkeypatch):
        async def _raising(pool, query, *, limit, mode):
            raise RuntimeError("pgvector unavailable")

        monkeypatch.setattr(delegation_ledger, "search_memory_catalog", _raising)
        target, match_id, score = await resolve_target_via_catalog(AsyncMock(), "question")
        assert (target, match_id, score) == (None, None, None)

    async def test_memory_module_not_loaded_returns_no_hits(self, monkeypatch):
        """No hook registered (memory module absent) -> [] hits, not an error."""
        monkeypatch.setattr(delegation_ledger, "search_memory_catalog", AsyncMock(return_value=[]))
        target, match_id, score = await resolve_target_via_catalog(AsyncMock(), "question")
        assert (target, match_id, score) == (None, None, None)
