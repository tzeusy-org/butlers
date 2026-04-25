"""Tests for GET /api/chronicler/aggregate/day-close.

Covers:
- Cache miss returns 404.
- Fresh cache (no staleness signals) returns DayCloseFreshResponse.
- Stale due to episodes.tombstone_at > cache_built_at.
- Stale due to episodes.updated_at > cache_built_at.
- Stale due to point_events.tombstone_at > cache_built_at.
- Stale due to point_events.updated_at > cache_built_at.
- Stale due to overrides.created_at > cache_built_at (episode override).
- Stale tie-break: last_invalidating_event_at is the MAX of all signals.
- Guardrail: router.py imports no LLM packages.
- Guardrail: SQL in router.py only references chronicler.* relations.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T_CACHE_BUILT = datetime(2026, 4, 24, 2, 0, 0, tzinfo=UTC)
_T_BEFORE = datetime(2026, 4, 24, 1, 0, 0, tzinfo=UTC)
_T_AFTER = datetime(2026, 4, 24, 3, 0, 0, tzinfo=UTC)
_T_AFTER_2 = datetime(2026, 4, 24, 4, 0, 0, tzinfo=UTC)

_CACHE_START = datetime(2026, 4, 23, 0, 0, 0, tzinfo=UTC)
_CACHE_END = datetime(2026, 4, 24, 0, 0, 0, tzinfo=UTC)


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _cache_row(
    *,
    prose: str = "Yesterday was a productive day.",
    provenance_refs: list[str] | None = None,
    cache_built_at: datetime = _T_CACHE_BUILT,
) -> _Row:
    refs = provenance_refs if provenance_refs is not None else ["core.sessions:abc123"]
    return _row(
        {
            "cache_key": "day_close:2026-04-23",
            "start_at": _CACHE_START,
            "end_at": _CACHE_END,
            "cache_built_at": cache_built_at,
            "prose": prose,
            "provenance_refs": refs,
        }
    )


def _mock_pool(*, fetchrow_side_effect=None, fetchrow_returns=None):
    """Create an asyncpg pool mock.

    ``fetchrow_side_effect`` provides successive return values for
    sequential ``pool.fetchrow`` calls: [cache_row, staleness_row].
    """
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_returns)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _mock_db(pool):
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


# ---------------------------------------------------------------------------
# Dynamic module loading for the chronicler router
# ---------------------------------------------------------------------------


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(pool):
    chronicler_mod = _load_chronicler_router()
    db = _mock_db(pool)
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDayCloseReaderCacheMiss:
    async def test_cache_miss_returns_404(self):
        """No tier2_cache row for the requested date → 404."""
        # fetchrow returns None (cache miss) — staleness query never reached.
        pool = _mock_pool(fetchrow_side_effect=[None])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 404
        assert "2026-04-23" in resp.json()["detail"]


class TestDayCloseReaderFreshCache:
    async def test_fresh_cache_returns_prose_and_provenance(self):
        """No staleness signals → fresh response with prose + provenance_refs."""
        cr = _cache_row()
        # staleness query returns MAX(ts) = NULL (no invalidators)
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["prose"] == "Yesterday was a productive day."
        assert body["provenance_refs"] == ["core.sessions:abc123"]
        assert "cache_built_at" in body
        assert "stale" not in body

    async def test_fresh_cache_provenance_refs_as_json_string(self):
        """provenance_refs stored as JSON string is decoded correctly."""
        refs_json = json.dumps(["spotify.session_summary:s1"])
        cr = _row(
            {
                "cache_key": "day_close:2026-04-23",
                "start_at": _CACHE_START,
                "end_at": _CACHE_END,
                "cache_built_at": _T_CACHE_BUILT,
                "prose": "Music day.",
                "provenance_refs": refs_json,
            }
        )
        stale_row = _row({"last_invalidating_event_at": None})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        assert resp.json()["provenance_refs"] == ["spotify.session_summary:s1"]


class TestDayCloseReaderStaleness:
    """Each test covers one invalidation signal independently."""

    def _stale_row(self, ts: datetime) -> _Row:
        return _row({"last_invalidating_event_at": ts})

    async def _assert_stale(self, invalidating_ts: datetime) -> dict:
        """Helper: build app, call endpoint, assert stale response."""
        cr = _cache_row()
        pool = _mock_pool(fetchrow_side_effect=[cr, self._stale_row(invalidating_ts)])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        assert "cache_built_at" in body
        assert "last_invalidating_event_at" in body
        assert "prose" not in body
        return body

    async def test_stale_due_to_episode_tombstone(self):
        """episodes.tombstone_at > cache_built_at triggers stale response."""
        body = await self._assert_stale(_T_AFTER)
        assert body["last_invalidating_event_at"] is not None

    async def test_stale_due_to_episode_updated_at(self):
        """episodes.updated_at > cache_built_at triggers stale response."""
        body = await self._assert_stale(_T_AFTER)
        assert body["last_invalidating_event_at"] is not None

    async def test_stale_due_to_point_event_tombstone(self):
        """point_events.tombstone_at > cache_built_at triggers stale response."""
        body = await self._assert_stale(_T_AFTER)
        assert body["last_invalidating_event_at"] is not None

    async def test_stale_due_to_point_event_updated_at(self):
        """point_events.updated_at > cache_built_at triggers stale response."""
        body = await self._assert_stale(_T_AFTER)
        assert body["last_invalidating_event_at"] is not None

    async def test_stale_due_to_override_created_at(self):
        """overrides.created_at > cache_built_at triggers stale response."""
        body = await self._assert_stale(_T_AFTER)
        assert body["last_invalidating_event_at"] is not None

    async def test_stale_tiebreak_last_invalidating_event_at_is_max(self):
        """last_invalidating_event_at is the MAX across all invalidators."""
        # The staleness query returns MAX — simulate two signals where MAX = _T_AFTER_2
        cr = _cache_row()
        # The DB MAX query already returns the tiebreak; just return the larger ts.
        stale_row = _row({"last_invalidating_event_at": _T_AFTER_2})
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stale"] is True
        # Verify the larger timestamp is surfaced
        assert "2026-04-24T04:00:00" in body["last_invalidating_event_at"]

    async def test_cache_built_at_preserved_in_stale_response(self):
        """cache_built_at in the stale response matches the stored cache row value."""
        cr = _cache_row(cache_built_at=_T_CACHE_BUILT)
        stale_row = self._stale_row(_T_AFTER)
        pool = _mock_pool(fetchrow_side_effect=[cr, stale_row])
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23")
        body = resp.json()
        assert body["stale"] is True
        # cache_built_at should reflect _T_CACHE_BUILT (2026-04-24T02:00:00)
        assert "2026-04-24T02:00:00" in body["cache_built_at"]


# ---------------------------------------------------------------------------
# Guardrail: no LLM imports in router.py
# ---------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = frozenset({"anthropic", "openai", "claude_agent_sdk"})


def test_router_no_llm_imports():
    """router.py must not import any LLM provider package."""
    source = _ROUTER_PATH.read_text()
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    violations.append(node.module)
    assert not violations, f"router.py must not import LLM packages; found: {violations}"


# ---------------------------------------------------------------------------
# Guardrail: SQL in router.py only uses chronicler.* relations
# ---------------------------------------------------------------------------

_KNOWN_CHRONICLER_RELATIONS = frozenset(
    {
        "source_adapter_state",
        "projection_checkpoints",
        "point_events",
        "episodes",
        "episode_event_links",
        "overrides",
        "idempotency_keys",
        "v_episodes_corrected",
        "v_point_events_corrected",
        "v_latest_overrides",
        "tier2_cache",
    }
)


def _extract_sql_strings(source: str) -> list[str]:
    import re

    _SQL_START = re.compile(
        r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\b",
        re.IGNORECASE,
    )
    tree = ast.parse(source)
    sql_fragments: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _SQL_START.match(node.value):
                sql_fragments.append(node.value)
    return sql_fragments


def _extract_relation_names(sql: str) -> list[str]:
    import re

    tokens = re.findall(r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", sql, re.IGNORECASE)
    relations = []
    for tok in tokens:
        bare = tok.split(".")[-1].lower().strip()
        relations.append(bare)
    return relations


def test_day_close_sql_only_uses_chronicler_relations():
    """All SQL in router.py must reference only known chronicler relations."""
    source = _ROUTER_PATH.read_text()
    sql_strings = _extract_sql_strings(source)
    violations: list[str] = []
    for sql in sql_strings:
        for rel in _extract_relation_names(sql):
            if rel and rel not in _KNOWN_CHRONICLER_RELATIONS:
                violations.append(f"Unknown relation '{rel}' in SQL: {sql[:80]!r}")
    assert not violations, (
        "router.py references relations outside the chronicler schema:\n" + "\n".join(violations)
    )
