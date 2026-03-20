"""Tests for shared memory catalog: store_fact/store_rule catalog write-behind
and search_catalog() cross-butler search function.

All tests use unit-level mocks — no live database required.
"""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load modules under test from disk (roster/ is not a Python package)
# ---------------------------------------------------------------------------


def _load_module(name: str):
    path = MEMORY_MODULE_PATH / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_storage_mod = _load_module("storage")
_search_mod = _load_module("search")

store_fact = _storage_mod.store_fact
store_rule = _storage_mod.store_rule
_upsert_catalog = _storage_mod._upsert_catalog
search_catalog = _search_mod.search_catalog

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper
# ---------------------------------------------------------------------------


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedding_engine():
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.fixture()
def mock_pool():
    """Return (pool, conn) mocks wired up like asyncpg."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])

    return pool, conn


# ---------------------------------------------------------------------------
# Tests: store_fact catalog write-behind
# ---------------------------------------------------------------------------


class TestStoreFACTCatalog:
    """store_fact() catalog write-behind behaviour."""

    async def test_catalog_skipped_when_flag_false(self, mock_pool, embedding_engine):
        """When enable_shared_catalog=False (default), no catalog upsert occurs."""
        pool, _conn = mock_pool
        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=False,
            source_schema="health",
        )
        # pool.execute is called for the canonical INSERT only (inside the transaction).
        # Verify _upsert_catalog was NOT called separately by checking pool.execute
        # was not called with shared.memory_catalog.
        for call in pool.execute.call_args_list:
            sql_arg = call.args[0] if call.args else ""
            assert "shared.memory_catalog" not in sql_arg

    async def test_catalog_skipped_when_no_source_schema(self, mock_pool, embedding_engine):
        """When source_schema is not provided, catalog write is skipped."""
        pool, _conn = mock_pool
        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema=None,
        )
        for call in pool.execute.call_args_list:
            sql_arg = call.args[0] if call.args else ""
            assert "shared.memory_catalog" not in sql_arg

    async def test_catalog_written_when_enabled(self, mock_pool, embedding_engine):
        """When enable_shared_catalog=True and source_schema provided, catalog upsert runs."""
        pool, _conn = mock_pool
        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
        )
        # At least one pool.execute call should reference shared.memory_catalog
        catalog_calls = [
            call
            for call in pool.execute.call_args_list
            if call.args and "shared.memory_catalog" in call.args[0]
        ]
        assert len(catalog_calls) == 1, "Expected exactly one catalog upsert"

    async def test_catalog_failure_does_not_raise(self, mock_pool, embedding_engine):
        """Catalog write failure must NOT propagate — canonical write succeeds."""
        pool, _conn = mock_pool

        async def _execute_side_effect(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                raise RuntimeError("Simulated catalog failure")
            return None

        pool.execute.side_effect = _execute_side_effect

        # Should NOT raise — catalog failure is logged as warning only
        result = await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
        )
        # store_fact() now returns a dict with 'id' (UUID) and optional keys
        assert isinstance(result, dict)
        assert isinstance(result["id"], uuid.UUID)

    async def test_catalog_failure_logged(self, mock_pool, embedding_engine, caplog):
        """Catalog write failure is logged at WARNING level."""
        pool, _conn = mock_pool

        async def _execute_side_effect(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                raise RuntimeError("Simulated catalog failure")
            return None

        pool.execute.side_effect = _execute_side_effect

        import logging

        with caplog.at_level(logging.WARNING, logger="butlers.modules.memory.storage"):
            await store_fact(
                pool,
                "Alice",
                "works_at",
                "Acme Corp",
                embedding_engine,
                enable_shared_catalog=True,
                source_schema="health",
            )

        assert any("memory_catalog" in rec.message for rec in caplog.records)

    async def test_catalog_contains_correct_metadata(self, mock_pool, embedding_engine):
        """Catalog upsert should use source_schema, source_table='facts', memory_type='fact'."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="myschema",
        )

        assert len(captured) == 1, "Expected one catalog upsert call"
        _sql, args = captured[0]
        # args: source_schema, source_table, source_id, source_butler, tenant_id,
        #       entity_id, summary, embedding_str, search_text, memory_type
        assert args[0] == "myschema"  # source_schema
        assert args[1] == "facts"  # source_table
        assert args[9] == "fact"  # memory_type


# ---------------------------------------------------------------------------
# Tests: store_rule catalog write-behind
# ---------------------------------------------------------------------------


class TestStoreRuleCatalog:
    """store_rule() catalog write-behind behaviour."""

    async def test_catalog_skipped_when_flag_false(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        await store_rule(
            pool,
            "Always greet users warmly",
            embedding_engine,
            enable_shared_catalog=False,
            source_schema="health",
        )
        for call in pool.execute.call_args_list:
            sql_arg = call.args[0] if call.args else ""
            assert "shared.memory_catalog" not in sql_arg

    async def test_catalog_written_when_enabled(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        await store_rule(
            pool,
            "Always greet users warmly",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="general",
        )
        catalog_calls = [
            call
            for call in pool.execute.call_args_list
            if call.args and "shared.memory_catalog" in call.args[0]
        ]
        assert len(catalog_calls) == 1

    async def test_catalog_failure_does_not_raise(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool

        async def _execute_side_effect(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                raise RuntimeError("Simulated catalog failure")
            return None

        pool.execute.side_effect = _execute_side_effect

        rule_id = await store_rule(
            pool,
            "Always greet users warmly",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="general",
        )
        assert isinstance(rule_id, uuid.UUID)

    async def test_rule_catalog_metadata(self, mock_pool, embedding_engine):
        """Rule catalog entry should use source_table='rules', memory_type='rule'."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_rule(
            pool,
            "Always greet users warmly",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="myschema",
        )

        assert len(captured) == 1
        _sql, args = captured[0]
        assert args[0] == "myschema"  # source_schema
        assert args[1] == "rules"  # source_table
        assert args[9] == "rule"  # memory_type


# ---------------------------------------------------------------------------
# Tests: fact catalog enrichment fields (core_024 spec columns)
# ---------------------------------------------------------------------------


class TestFactCatalogEnrichmentFields:
    """store_fact() passes spec-required enrichment columns to _upsert_catalog."""

    async def _capture_catalog_args(self, pool, **store_kwargs) -> tuple:
        """Run store_fact and return the positional args passed to the catalog INSERT."""
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute
        return captured

    async def test_fact_title_is_subject_predicate(self, mock_pool, embedding_engine):
        """title must be '{subject} {predicate}' per spec."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
        )

        assert len(captured) == 1
        _sql, args = captured[0]
        # args order: source_schema[0], source_table[1], source_id[2], source_butler[3],
        # tenant_id[4], entity_id[5], summary[6], embedding[7], search_text[8],
        # memory_type[9], title[10], predicate[11], scope[12], valid_at[13],
        # confidence[14], importance[15], retention_class[16], sensitivity[17],
        # object_entity_id[18]
        assert args[10] == "Alice works_at", f"title mismatch: {args[10]!r}"

    async def test_fact_predicate_column_populated(self, mock_pool, embedding_engine):
        """predicate column must contain the fact's predicate."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
        )

        _sql, args = captured[0]
        assert args[11] == "works_at", f"predicate mismatch: {args[11]!r}"

    async def test_fact_scope_column_populated(self, mock_pool, embedding_engine):
        """scope column must reflect the fact's scope."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
            scope="professional",
        )

        _sql, args = captured[0]
        assert args[12] == "professional", f"scope mismatch: {args[12]!r}"

    async def test_fact_importance_column_populated(self, mock_pool, embedding_engine):
        """importance column must reflect the fact's importance value."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
            importance=8.5,
        )

        _sql, args = captured[0]
        assert args[15] == 8.5, f"importance mismatch: {args[15]!r}"

    async def test_fact_confidence_is_one(self, mock_pool, embedding_engine):
        """confidence must be 1.0 for newly stored facts."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_fact(
            pool,
            "Alice",
            "works_at",
            "Acme Corp",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
        )

        _sql, args = captured[0]
        assert args[14] == 1.0, f"confidence mismatch: {args[14]!r}"

    async def test_fact_object_entity_id_propagated(self, mock_pool, embedding_engine):
        """object_entity_id must be forwarded to the catalog upsert."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        entity_id = uuid.uuid4()
        obj_entity_id = uuid.uuid4()

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        # validate entity existence checks happen on the conn, not pool
        pool.execute.side_effect = _capture_execute
        conn = _conn
        # Entity validation now uses fetchrow (SELECT id, entity_type).
        # Side effect order: entity check, object entity check, registry lookup, supersession.
        conn.fetchrow = AsyncMock(
            side_effect=[
                {"id": entity_id, "entity_type": "person"},
                {"id": obj_entity_id, "entity_type": "person"},
                None,  # registry lookup (novel predicate)
                None,  # supersession check
            ]
        )
        conn.execute = AsyncMock(return_value=None)

        await store_fact(
            pool,
            "Alice",
            "knows",
            "Bob",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="health",
            entity_id=entity_id,
            object_entity_id=obj_entity_id,
        )

        _sql, args = captured[0]
        assert args[18] == obj_entity_id, f"object_entity_id mismatch: {args[18]!r}"


# ---------------------------------------------------------------------------
# Tests: rule catalog enrichment fields (core_024 spec columns)
# ---------------------------------------------------------------------------


class TestRuleCatalogEnrichmentFields:
    """store_rule() passes spec-required enrichment columns to _upsert_catalog."""

    async def test_rule_title_is_first_100_chars(self, mock_pool, embedding_engine):
        """title must be content[:100] for rules per spec."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        content = "Always greet users warmly"
        await store_rule(
            pool,
            content,
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="general",
        )

        assert len(captured) == 1
        _sql, args = captured[0]
        # args[10] is title
        assert args[10] == content[:100], f"title mismatch: {args[10]!r}"

    async def test_rule_title_truncated_to_100_chars(self, mock_pool, embedding_engine):
        """title must be truncated to 100 characters for long rule content."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        long_content = "A" * 200
        await store_rule(
            pool,
            long_content,
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="general",
        )

        _sql, args = captured[0]
        assert args[10] == "A" * 100, "title must be truncated to 100 chars"
        assert len(args[10]) == 100

    async def test_rule_scope_column_populated(self, mock_pool, embedding_engine):
        """scope column must reflect the rule's scope."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_rule(
            pool,
            "Always greet users warmly",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="general",
            scope="communication",
        )

        _sql, args = captured[0]
        assert args[12] == "communication", f"scope mismatch: {args[12]!r}"

    async def test_rule_predicate_is_none(self, mock_pool, embedding_engine):
        """predicate must be None for rules (rules have no subject/predicate structure)."""
        pool, _conn = mock_pool
        captured: list[tuple] = []

        async def _capture_execute(sql, *args, **kwargs):
            if "shared.memory_catalog" in sql:
                captured.append((sql, args))
            return None

        pool.execute.side_effect = _capture_execute

        await store_rule(
            pool,
            "Always greet users warmly",
            embedding_engine,
            enable_shared_catalog=True,
            source_schema="general",
        )

        _sql, args = captured[0]
        assert args[11] is None, f"predicate should be None for rules, got: {args[11]!r}"


# ---------------------------------------------------------------------------
# Tests: search_catalog()
# ---------------------------------------------------------------------------


class TestSearchCatalog:
    """search_catalog() cross-butler search function."""

    async def test_invalid_mode_raises(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        with pytest.raises(ValueError, match="Invalid mode"):
            await search_catalog(pool, "query", embedding_engine, mode="bad")

    async def test_semantic_mode_calls_pool_fetch(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        pool.fetch = AsyncMock(return_value=[])
        results = await search_catalog(pool, "health tips", embedding_engine, mode="semantic")
        assert results == []
        assert pool.fetch.call_count == 1
        # Verify the query includes shared.memory_catalog
        sql_called = pool.fetch.call_args.args[0]
        assert "shared.memory_catalog" in sql_called
        assert "embedding <=>" in sql_called

    async def test_keyword_mode_calls_pool_fetch(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        pool.fetch = AsyncMock(return_value=[])
        results = await search_catalog(pool, "health tips", embedding_engine, mode="keyword")
        assert results == []
        assert pool.fetch.call_count == 1
        sql_called = pool.fetch.call_args.args[0]
        assert "shared.memory_catalog" in sql_called
        assert "search_vector" in sql_called

    async def test_hybrid_mode_calls_pool_fetch_twice(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        pool.fetch = AsyncMock(return_value=[])
        results = await search_catalog(pool, "health tips", embedding_engine, mode="hybrid")
        assert results == []
        assert pool.fetch.call_count == 2

    async def test_memory_type_filter_applied(self, mock_pool, embedding_engine):
        """When memory_type is set, it should appear in the WHERE clause parameters."""
        pool, _conn = mock_pool
        pool.fetch = AsyncMock(return_value=[])
        await search_catalog(
            pool, "health tips", embedding_engine, mode="semantic", memory_type="fact"
        )
        call_params = pool.fetch.call_args.args
        assert "fact" in call_params

    async def test_tenant_id_applied(self, mock_pool, embedding_engine):
        pool, _conn = mock_pool
        pool.fetch = AsyncMock(return_value=[])
        await search_catalog(
            pool, "health tips", embedding_engine, mode="semantic", tenant_id="tenant_x"
        )
        call_params = pool.fetch.call_args.args
        assert "tenant_x" in call_params

    async def test_hybrid_rrf_fusion(self, mock_pool, embedding_engine):
        """Hybrid mode fuses semantic and keyword results via RRF."""
        pool, _conn = mock_pool

        # Two rows: one in semantic only, one in keyword only, one in both.
        shared_id = uuid.uuid4()
        sem_only_id = uuid.uuid4()
        kw_only_id = uuid.uuid4()

        def _make_row(rid, extra=None):
            row = MagicMock()
            data = {
                "id": rid,
                "source_schema": "health",
                "source_table": "facts",
                "source_id": uuid.uuid4(),
                "memory_type": "fact",
                "summary": "test",
                "tenant_id": "owner",
                "similarity": 0.9,
                "rank": 1.0,
            }
            if extra:
                data.update(extra)
            row.keys.return_value = data.keys()
            row.__iter__ = lambda self: iter(data.items())
            # Make dict(row) work
            row.items = data.items
            return data

        sem_rows = [_make_row(shared_id), _make_row(sem_only_id)]
        kw_rows = [_make_row(shared_id), _make_row(kw_only_id)]

        # Return appropriate rows for each fetch call
        _fetch_call_idx = {"n": 0}

        async def _fetch_side_effect(sql, *args):
            # Build asyncpg-like records from dicts
            records = []
            data_list = sem_rows if _fetch_call_idx["n"] == 0 else kw_rows
            _fetch_call_idx["n"] += 1
            for data in data_list:
                rec = MagicMock()
                rec.__iter__ = lambda self, d=data: iter(d.items())
                rec.keys.return_value = list(data.keys())
                # Make dict(rec) work by using __iter__
                records.append(data)
            return records

        pool.fetch.side_effect = _fetch_side_effect

        results = await search_catalog(pool, "health tips", embedding_engine, mode="hybrid")
        # All 3 unique IDs should appear
        result_ids = {r["id"] for r in results}
        assert shared_id in result_ids
        assert sem_only_id in result_ids
        assert kw_only_id in result_ids

    async def test_empty_query_keyword_returns_empty(self, mock_pool, embedding_engine):
        """Keyword search with empty query (after preprocessing) returns empty list."""
        pool, _conn = mock_pool
        pool.fetch = AsyncMock(return_value=[])
        results = await search_catalog(pool, "   ", embedding_engine, mode="keyword")
        # Keyword search preprocesses to empty → no fetch called, returns []
        assert results == []
        # pool.fetch should NOT have been called (preprocessed query is empty)
        pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: MemoryModuleConfig feature flag
# ---------------------------------------------------------------------------


class TestMemoryModuleConfigCatalogFlag:
    """Verify MemoryModuleConfig exposes enable_shared_catalog feature flag."""

    def test_default_is_false(self):
        from butlers.modules.memory import MemoryModuleConfig

        config = MemoryModuleConfig()
        assert config.enable_shared_catalog is False

    def test_can_be_enabled(self):
        from butlers.modules.memory import MemoryModuleConfig

        config = MemoryModuleConfig(enable_shared_catalog=True)
        assert config.enable_shared_catalog is True

    def test_catalog_source_schema_default_empty(self):
        from butlers.modules.memory import MemoryModuleConfig

        config = MemoryModuleConfig()
        assert config.catalog_source_schema == ""
