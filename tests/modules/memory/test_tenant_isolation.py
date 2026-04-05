"""Behavioral tests for tenant isolation in memory storage and search.

Verifies that tenant_id is properly scoped at every layer:
  - Storage INSERT includes tenant_id (shared by default)
  - Supersession check is scoped to tenant
  - Search queries include tenant_id WHERE clause
  - Tool layer propagates request_context -> tenant_id to storage
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

pytestmark = pytest.mark.unit

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"
_SEARCH_PATH = MEMORY_MODULE_PATH / "search.py"
_TOOLS_WRITING_PATH = MEMORY_MODULE_PATH / "tools" / "writing.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_storage = _load("storage", _STORAGE_PATH)
_search = _load("search", _SEARCH_PATH)


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


@pytest.fixture()
def embed() -> MagicMock:
    e = MagicMock()
    e.embed.return_value = [0.1] * 384
    return e


@pytest.fixture()
def simple_pool() -> AsyncMock:
    return AsyncMock()


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
# Storage: tenant_id in INSERT
# ---------------------------------------------------------------------------


class TestStorageTenantLineage:
    async def test_episode_defaults_to_shared(
        self, simple_pool: AsyncMock, embed: MagicMock
    ) -> None:
        await _storage.store_episode(simple_pool, "text", "butler", embed)
        sql, *args = simple_pool.execute.call_args[0]
        assert "tenant_id" in sql and "shared" in args

    async def test_episode_custom_tenant(self, simple_pool: AsyncMock, embed: MagicMock) -> None:
        await _storage.store_episode(simple_pool, "t", "b", embed, tenant_id="tenant-a")
        _, *args = simple_pool.execute.call_args[0]
        assert "tenant-a" in args

    async def test_fact_custom_tenant_in_insert(self, fact_pool, embed: MagicMock) -> None:
        pool, conn = fact_pool
        await _storage.store_fact(pool, "user", "city", "Berlin", embed, tenant_id="tenant-b")
        all_args = conn.execute.call_args_list[0].args
        assert "tenant-b" in all_args

    async def test_supersession_scoped_to_tenant(self, embed: MagicMock) -> None:
        existing_id = uuid.uuid4()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(side_effect=[None, None, {"id": existing_id}])
        conn.execute = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))
        await _storage.store_fact(pool, "user", "city", "Munich", embed, tenant_id="tenant-a")
        fetchrow_args = conn.fetchrow.call_args[0]
        assert fetchrow_args[1] == "tenant-a"


# ---------------------------------------------------------------------------
# Search: tenant_id WHERE clause
# ---------------------------------------------------------------------------


class TestSearchTenantFilter:
    async def test_semantic_search_includes_tenant_param(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await _search.semantic_search(pool, [0.1] * 384, "facts", tenant_id="tenant-x")
        sql = pool.fetch.call_args[0][0]
        params = pool.fetch.call_args[0]
        assert "tenant_id" in sql and "tenant-x" in params

    async def test_keyword_search_includes_tenant_param(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await _search.keyword_search(pool, "coffee", "facts", tenant_id="tenant-x")
        sql = pool.fetch.call_args[0][0]
        assert "tenant_id" in sql


# ---------------------------------------------------------------------------
# Tool layer: request_context propagation
# ---------------------------------------------------------------------------


class TestWritingToolRequestContext:
    def _load_writing_tools(self):
        mock_storage = MagicMock()
        mock_storage.store_episode = AsyncMock(return_value=uuid.uuid4())
        mock_storage.store_fact = AsyncMock(return_value=uuid.uuid4())
        mock_storage.store_rule = AsyncMock(return_value=uuid.uuid4())
        mock_storage._DEFAULT_EPISODE_TTL_DAYS = 7
        spec = importlib.util.spec_from_file_location("writing", _TOOLS_WRITING_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, mock_storage

    async def test_no_request_context_defaults_to_shared(self) -> None:
        mod, mock_storage = self._load_writing_tools()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_episode(pool, "text", "butler")
        kw = mock_storage.store_episode.call_args[1]
        assert kw["tenant_id"] == "shared" and kw["request_id"] is None

    async def test_request_context_propagates_tenant_and_request_id(self) -> None:
        mod, mock_storage = self._load_writing_tools()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        with patch.object(mod, "_storage", mock_storage):
            await mod.memory_store_episode(
                pool,
                "text",
                "butler",
                request_context={"tenant_id": "finance", "request_id": "req-123"},
            )
        kw = mock_storage.store_episode.call_args[1]
        assert kw["tenant_id"] == "finance" and kw["request_id"] == "req-123"

    async def test_invalid_tenant_rejected(self) -> None:
        mod, mock_storage = self._load_writing_tools()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=None)
        with patch.object(mod, "_storage", mock_storage), pytest.raises(ValueError, match="owner"):
            await mod.memory_store_episode(
                pool,
                "text",
                "butler",
                request_context={"tenant_id": "owner"},
            )
