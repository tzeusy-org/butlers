"""Tests for the ``memory_access`` core MCP tool.

Covers:
- Butler without memory module: returns empty read/write lists.
- Butler with memory module and pool: returns all three stores plus drops_7d.
- drops_7d aggregates facts/episodes/rules drop counts.
- DB query failure degrades gracefully (drops_7d = 0).
- embedding_model sourced from module config when available.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.core_tools._base import ToolContext
from butlers.core_tools._memory_access import register_memory_access_tool

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(facts_dropped=0, episodes_dropped=0, rules_dropped=0, raises=False):
    """Return an AsyncMock pool that returns seeded drop counts."""
    pool = AsyncMock()

    if raises:
        pool.fetchval = AsyncMock(side_effect=RuntimeError("db error"))
    else:
        # fetchval is called three times: facts, episodes, rules.
        pool.fetchval = AsyncMock(side_effect=[facts_dropped, episodes_dropped, rules_dropped])
    return pool


def _make_memory_module(embedding_model=None):
    """Return a minimal module-like object with name='memory'."""
    cfg = SimpleNamespace(embedding_model=embedding_model)
    return SimpleNamespace(name="memory", _config=cfg)


def _register_and_grab(daemon, pool=None):
    """Register memory_access on a minimal daemon and return the tool function."""
    registered: dict = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    mcp = SimpleNamespace()
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=None,
        butler_name="memory-butler",
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_memory_access_tool(ctx, mcp, _core_tool)
    return registered["memory_access"]


# ---------------------------------------------------------------------------
# Tests: no memory module
# ---------------------------------------------------------------------------


async def test_memory_access_no_module_returns_empty():
    """When no memory module is loaded, read/write are empty lists."""
    daemon = SimpleNamespace(_modules=[])
    tool = _register_and_grab(daemon, pool=None)

    result = await tool()

    assert result["read"] == []
    assert result["write"] == []
    assert result["namespace"] is None
    assert result["embedding_model"] is None
    assert result["drops_7d"] == 0


async def test_memory_access_no_pool_returns_empty():
    """When pool is None even with a memory module, degrades to empty lists."""
    daemon = SimpleNamespace(_modules=[_make_memory_module()])
    tool = _register_and_grab(daemon, pool=None)

    result = await tool()

    assert result["read"] == []
    assert result["write"] == []
    assert result["drops_7d"] == 0


# ---------------------------------------------------------------------------
# Tests: memory module present
# ---------------------------------------------------------------------------


async def test_memory_access_with_module_returns_all_stores():
    """When memory module is present, read and write list all three stores."""
    daemon = SimpleNamespace(_modules=[_make_memory_module()])
    pool = _make_pool(facts_dropped=2, episodes_dropped=1, rules_dropped=0)
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert set(result["read"]) == {"episodes", "facts", "rules"}
    assert set(result["write"]) == {"episodes", "facts", "rules"}
    assert result["namespace"] == "memory-butler"
    assert result["drops_7d"] == 3  # 2 + 1 + 0


async def test_memory_access_drops_7d_aggregates_all_tables():
    """drops_7d sums expired facts, expired episodes, and forgotten rules."""
    daemon = SimpleNamespace(_modules=[_make_memory_module()])
    pool = _make_pool(facts_dropped=5, episodes_dropped=3, rules_dropped=2)
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert result["drops_7d"] == 10  # 5 + 3 + 2


async def test_memory_access_drops_7d_zero_when_nothing_dropped():
    """drops_7d is 0 when no memories were dropped in the window."""
    daemon = SimpleNamespace(_modules=[_make_memory_module()])
    pool = _make_pool(facts_dropped=0, episodes_dropped=0, rules_dropped=0)
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert result["drops_7d"] == 0


async def test_memory_access_drops_7d_degrades_on_db_error():
    """When the drops_7d DB query fails, drops_7d falls back to 0 without raising.

    The memory stores are still returned because the module is loaded; only
    the drop count degrades.
    """
    daemon = SimpleNamespace(_modules=[_make_memory_module()])
    pool = _make_pool(raises=True)
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert result["drops_7d"] == 0
    # Memory stores are still accessible — only the stat query degraded.
    assert set(result["read"]) == {"episodes", "facts", "rules"}
    assert set(result["write"]) == {"episodes", "facts", "rules"}


# ---------------------------------------------------------------------------
# Tests: embedding_model resolution
# ---------------------------------------------------------------------------


async def test_memory_access_embedding_model_from_config():
    """embedding_model is read from module._config.embedding_model when set."""
    mod = _make_memory_module(embedding_model="text-embedding-3-small")
    daemon = SimpleNamespace(_modules=[mod])
    pool = _make_pool()
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert result["embedding_model"] == "text-embedding-3-small"


async def test_memory_access_embedding_model_defaults_to_minilm():
    """When config.embedding_model is None, falls back to all-MiniLM-L6-v2."""
    mod = _make_memory_module(embedding_model=None)
    daemon = SimpleNamespace(_modules=[mod])
    pool = _make_pool()
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert result["embedding_model"] == "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Tests: multiple modules — only memory module counts
# ---------------------------------------------------------------------------


async def test_memory_access_ignores_non_memory_modules():
    """Only a module with name='memory' triggers memory access; others are ignored."""
    other_mod = SimpleNamespace(name="telegram", _config=None)
    daemon = SimpleNamespace(_modules=[other_mod])
    pool = _make_pool()
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert result["read"] == []
    assert result["write"] == []


async def test_memory_access_with_mixed_modules():
    """Butler with memory + other modules still returns memory access correctly."""
    other_mod = SimpleNamespace(name="telegram", _config=None)
    mem_mod = _make_memory_module(embedding_model="all-MiniLM-L6-v2")
    daemon = SimpleNamespace(_modules=[other_mod, mem_mod])
    pool = _make_pool(facts_dropped=1, episodes_dropped=1, rules_dropped=1)
    tool = _register_and_grab(daemon, pool=pool)

    result = await tool()

    assert set(result["read"]) == {"episodes", "facts", "rules"}
    assert result["drops_7d"] == 3
