"""Tests for tenant isolation in memory storage and search.

Verifies that:
1. store_episode/store_fact/store_rule accept tenant_id and request_id.
2. The SQL INSERT includes tenant_id and request_id columns.
3. search functions add a tenant_id WHERE clause.
4. Tenant isolation: a fact stored for tenant A is not returned by tenant B search.
5. memory_store_* tool wrappers propagate request_context correctly.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load modules from disk
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"
_SEARCH_PATH = MEMORY_MODULE_PATH / "search.py"
_TOOLS_WRITING_PATH = MEMORY_MODULE_PATH / "tools" / "writing.py"


def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_storage = _load_module_from_path("storage", _STORAGE_PATH)
_search = _load_module_from_path("search", _SEARCH_PATH)


# ---------------------------------------------------------------------------
# Async context manager helper
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

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
def embedding_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.fixture()
def simple_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture()
def fact_pool():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool, conn


# ---------------------------------------------------------------------------
# Storage layer — tenant_id and request_id in INSERT
# ---------------------------------------------------------------------------


class TestStoreEpisodeTenantLineage:
    """store_episode includes tenant_id and request_id in the INSERT."""

    async def test_default_tenant_id_is_owner(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """When tenant_id is not specified, it defaults to 'owner'."""
        await _storage.store_episode(simple_pool, "test content", "test-butler", embedding_engine)
        sql, *args = simple_pool.execute.call_args[0]
        assert "tenant_id" in sql
        assert "owner" in args

    async def test_custom_tenant_id_is_stored(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """Custom tenant_id is passed through to the INSERT."""
        await _storage.store_episode(
            simple_pool,
            "test content",
            "test-butler",
            embedding_engine,
            tenant_id="tenant-a",
        )
        sql, *args = simple_pool.execute.call_args[0]
        assert "tenant_id" in sql
        assert "tenant-a" in args

    async def test_request_id_is_stored(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """Provided request_id is passed through to the INSERT."""
        await _storage.store_episode(
            simple_pool,
            "test content",
            "test-butler",
            embedding_engine,
            request_id="req-abc-123",
        )
        sql, *args = simple_pool.execute.call_args[0]
        assert "request_id" in sql
        assert "req-abc-123" in args

    async def test_request_id_defaults_to_none(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """When request_id is omitted, None is passed in the INSERT."""
        await _storage.store_episode(simple_pool, "test content", "test-butler", embedding_engine)
        _sql, *args = simple_pool.execute.call_args[0]
        # Bind params: $1=id, $2=butler, $3=session_id, $4=content, $5=embedding,
        # $6=search_text, $7=importance, $8=expires_at, $9=metadata, $10=tenant_id,
        # $11=request_id, $12=retention_class, $13=sensitivity
        assert args[10] is None  # request_id ($11, 0-based index 10)


class TestStoreFactTenantLineage:
    """store_fact includes tenant_id and request_id in the INSERT and scopes supersession."""

    async def test_default_tenant_id_is_owner(self, fact_pool, embedding_engine: MagicMock) -> None:
        """Default tenant_id is 'owner'."""
        pool, conn = fact_pool
        await _storage.store_fact(pool, "user", "city", "Berlin", embedding_engine)
        insert_sql = conn.execute.call_args_list[0].args[0]
        assert "tenant_id" in insert_sql
        # tenant_id is $21 — find the value in the positional args
        all_args = conn.execute.call_args_list[0].args
        assert "owner" in all_args

    async def test_custom_tenant_id_in_insert(self, fact_pool, embedding_engine: MagicMock) -> None:
        """Custom tenant_id flows into INSERT args."""
        pool, conn = fact_pool
        await _storage.store_fact(
            pool, "user", "city", "Berlin", embedding_engine, tenant_id="tenant-b"
        )
        all_args = conn.execute.call_args_list[0].args
        assert "tenant-b" in all_args

    async def test_supersession_check_includes_tenant_id(
        self, fact_pool, embedding_engine: MagicMock
    ) -> None:
        """The supersession lookup query includes a tenant_id filter."""
        pool, conn = fact_pool
        await _storage.store_fact(
            pool, "user", "city", "Berlin", embedding_engine, tenant_id="tenant-a"
        )
        # The fetchrow call (supersession check) should include tenant_id
        fetchrow_sql = conn.fetchrow.call_args[0][0]
        assert "tenant_id" in fetchrow_sql

    async def test_supersession_scoped_to_tenant(self, embedding_engine: MagicMock) -> None:
        """Supersession check passes tenant_id as the first WHERE param."""
        existing_id = uuid.uuid4()

        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value={"id": existing_id})
        conn.execute = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        await _storage.store_fact(
            pool, "user", "city", "Munich", embedding_engine, tenant_id="tenant-a"
        )
        # fetchrow call: first positional after SQL should be tenant_id
        fetchrow_args = conn.fetchrow.call_args[0]
        assert fetchrow_args[1] == "tenant-a"

    async def test_request_id_in_insert(self, fact_pool, embedding_engine: MagicMock) -> None:
        """request_id flows into INSERT args."""
        pool, conn = fact_pool
        await _storage.store_fact(
            pool, "user", "city", "Berlin", embedding_engine, request_id="req-xyz"
        )
        all_args = conn.execute.call_args_list[0].args
        assert "req-xyz" in all_args


class TestStoreRuleTenantLineage:
    """store_rule includes tenant_id and request_id in the INSERT."""

    async def test_default_tenant_id_is_owner(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """Default tenant_id is 'owner'."""
        await _storage.store_rule(simple_pool, "Always greet politely", embedding_engine)
        sql, *args = simple_pool.execute.call_args[0]
        assert "tenant_id" in sql
        assert "owner" in args

    async def test_custom_tenant_id_in_insert(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """Custom tenant_id flows into INSERT args."""
        await _storage.store_rule(
            simple_pool,
            "Always greet politely",
            embedding_engine,
            tenant_id="tenant-c",
        )
        _sql, *args = simple_pool.execute.call_args[0]
        assert "tenant-c" in args

    async def test_request_id_in_insert(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """request_id flows into INSERT args."""
        await _storage.store_rule(
            simple_pool,
            "Always greet politely",
            embedding_engine,
            request_id="req-rule-1",
        )
        _sql, *args = simple_pool.execute.call_args[0]
        assert "req-rule-1" in args


# ---------------------------------------------------------------------------
# Search layer — tenant_id WHERE clause
# ---------------------------------------------------------------------------


class TestSemanticSearchTenantFilter:
    """semantic_search adds a tenant_id = $N WHERE condition."""

    async def test_tenant_id_in_query(self) -> None:
        """semantic_search SQL includes a tenant_id filter."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        embedding = [0.1] * 384

        await _search.semantic_search(pool, embedding, "facts", tenant_id="tenant-a")

        sql = pool.fetch.call_args[0][0]
        assert "tenant_id" in sql

    async def test_tenant_id_value_passed_as_param(self) -> None:
        """The tenant_id value is passed as a query parameter."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        embedding = [0.1] * 384

        await _search.semantic_search(pool, embedding, "facts", tenant_id="tenant-x")

        call_args = pool.fetch.call_args[0]
        # Params after SQL: $1=embedding, $2=tenant_id, ...
        assert "tenant-x" in call_args


class TestKeywordSearchTenantFilter:
    """keyword_search adds a tenant_id = $N WHERE condition."""

    async def test_tenant_id_in_query(self) -> None:
        """keyword_search SQL includes a tenant_id filter."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        await _search.keyword_search(pool, "coffee", "facts", tenant_id="tenant-a")

        sql = pool.fetch.call_args[0][0]
        assert "tenant_id" in sql

    async def test_tenant_id_value_passed_as_param(self) -> None:
        """The tenant_id value is passed as a query parameter."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        await _search.keyword_search(pool, "coffee", "facts", tenant_id="tenant-x")

        call_args = pool.fetch.call_args[0]
        assert "tenant-x" in call_args


# ---------------------------------------------------------------------------
# Tenant isolation: fact stored under tenant-A not returned for tenant-B
# ---------------------------------------------------------------------------


class TestTenantIsolationLogic:
    """End-to-end isolation: different tenant_ids see different rows."""

    async def test_tenant_a_fact_not_returned_for_tenant_b_semantic(self) -> None:
        """semantic_search uses tenant_id param — tenant B won't see tenant A rows.

        This test verifies the SQL param routing: when tenant='tenant-b', the query
        parameter passed to the DB is 'tenant-b', not 'tenant-a'.
        """
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        embedding = [0.1] * 384

        # Search as tenant-b
        await _search.semantic_search(pool, embedding, "facts", tenant_id="tenant-b")

        call_args = pool.fetch.call_args[0]
        # Verify 'tenant-b' is in the params and 'tenant-a' is NOT
        assert "tenant-b" in call_args
        assert "tenant-a" not in call_args

    async def test_tenant_a_fact_not_returned_for_tenant_b_keyword(self) -> None:
        """keyword_search uses tenant_id param — tenant B won't see tenant A rows."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        await _search.keyword_search(pool, "berlin city", "facts", tenant_id="tenant-b")

        call_args = pool.fetch.call_args[0]
        assert "tenant-b" in call_args
        assert "tenant-a" not in call_args

    async def test_search_fn_propagates_tenant_to_hybrid(self) -> None:
        """search() propagates tenant_id down to hybrid_search/semantic_search."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        await _search.search(pool, "test query", engine, types=["fact"], tenant_id="tenant-z")

        # pool.fetch is called by semantic_search and keyword_search inside hybrid
        assert pool.fetch.called
        # All fetch calls should include 'tenant-z'
        for call in pool.fetch.call_args_list:
            params = call[0]
            assert "tenant-z" in params, f"Expected 'tenant-z' in {params}"


# ---------------------------------------------------------------------------
# Writing tool layer — request_context extraction
# ---------------------------------------------------------------------------


class TestWritingToolRequestContext:
    """memory_store_* tool functions extract tenant_id/request_id from request_context."""

    def _load_writing_tools(self):
        """Load tools/writing.py with mocked storage."""
        mock_storage = MagicMock()
        mock_storage.store_episode = AsyncMock(return_value=uuid.uuid4())
        mock_storage.store_fact = AsyncMock(return_value=uuid.uuid4())
        mock_storage.store_rule = AsyncMock(return_value=uuid.uuid4())
        mock_storage._DEFAULT_EPISODE_TTL_DAYS = 7

        spec = importlib.util.spec_from_file_location("writing", _TOOLS_WRITING_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        # Patch the _storage reference in the module's _helpers
        spec.loader.exec_module(mod)
        return mod, mock_storage

    async def test_store_episode_no_request_context_defaults(self) -> None:
        """Without request_context, tenant_id='owner' and request_id=None."""
        mod, mock_storage = self._load_writing_tools()

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)

        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_episode(pool, "episode text", "butler-x")

        kwargs = mock_storage.store_episode.call_args[1]
        assert kwargs.get("tenant_id") == "owner"
        assert kwargs.get("request_id") is None

    async def test_store_episode_with_request_context(self) -> None:
        """request_context values are extracted and passed to storage."""
        mod, mock_storage = self._load_writing_tools()

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)

        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_episode(
                pool,
                "episode text",
                "butler-x",
                request_context={"tenant_id": "tenant-a", "request_id": "req-123"},
            )

        kwargs = mock_storage.store_episode.call_args[1]
        assert kwargs.get("tenant_id") == "tenant-a"
        assert kwargs.get("request_id") == "req-123"

    async def test_store_fact_with_request_context(self) -> None:
        """request_context values reach storage.store_fact."""
        mod, mock_storage = self._load_writing_tools()

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_fact(
                pool,
                engine,
                "user",
                "city",
                "Berlin",
                request_context={"tenant_id": "tenant-b", "request_id": "req-456"},
            )

        kwargs = mock_storage.store_fact.call_args[1]
        assert kwargs.get("tenant_id") == "tenant-b"
        assert kwargs.get("request_id") == "req-456"

    async def test_store_rule_with_request_context(self) -> None:
        """request_context values reach storage.store_rule."""
        mod, mock_storage = self._load_writing_tools()

        pool = AsyncMock()
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_rule(
                pool,
                engine,
                "Always confirm before deleting",
                request_context={"tenant_id": "tenant-c", "request_id": "req-789"},
            )

        kwargs = mock_storage.store_rule.call_args[1]
        assert kwargs.get("tenant_id") == "tenant-c"
        assert kwargs.get("request_id") == "req-789"

    async def test_store_fact_no_request_context_defaults(self) -> None:
        """Without request_context, store_fact defaults to tenant='owner', request_id=None."""
        mod, mock_storage = self._load_writing_tools()

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_fact(pool, engine, "user", "city", "Berlin")

        kwargs = mock_storage.store_fact.call_args[1]
        assert kwargs.get("tenant_id") == "owner"
        assert kwargs.get("request_id") is None

    async def test_store_rule_no_request_context_defaults(self) -> None:
        """Without request_context, store_rule defaults to tenant='owner', request_id=None."""
        mod, mock_storage = self._load_writing_tools()

        pool = AsyncMock()
        engine = MagicMock()
        engine.embed.return_value = [0.1] * 384

        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_rule(pool, engine, "Always confirm before deleting")

        kwargs = mock_storage.store_rule.call_args[1]
        assert kwargs.get("tenant_id") == "owner"
        assert kwargs.get("request_id") is None
