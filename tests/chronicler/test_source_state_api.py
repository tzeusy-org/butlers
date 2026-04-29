"""Integration tests for GET /api/chronicler/source-state.

Covers:
- 200 OK with populated source_adapter_state rows
- 200 OK with empty data: [] on cold boot
- Per-subsource checkpoint detail joined correctly
- latest last_run_at / last_error aggregated across subsources
- 405 Method Not Allowed for non-GET verbs
- No SQL outside chronicler.* schema (guardrail)
"""

from __future__ import annotations

import ast
import importlib.util
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

_NOW = datetime.now(tz=UTC)
_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _mock_pool(*, fetch_side_effect=None, fetch_rows=None):
    """Create an asyncpg pool mock.

    Pass ``fetch_side_effect`` to return different values on successive
    ``pool.fetch`` calls (list of return values).  Falls back to ``fetch_rows``
    for a single-call scenario.
    """
    pool = AsyncMock()
    if fetch_side_effect is not None:
        pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _mock_db(pool):
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


# ---------------------------------------------------------------------------
# Dynamic module loading for the chronicler router (mirrors router_discovery)
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


def _make_app(*, fetch_side_effect=None, fetch_rows=None):
    chronicler_mod = _load_chronicler_router()
    pool = _mock_pool(fetch_side_effect=fetch_side_effect, fetch_rows=fetch_rows)
    db = _mock_db(pool)

    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app, pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSourceStateAPI:
    async def test_empty_table_returns_200_with_empty_data(self):
        """Cold-boot: source_adapter_state empty → 200 with data: []."""
        # Both fetches (adapter rows, checkpoint rows) return empty lists.
        app, _ = _make_app(fetch_side_effect=[[], []])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert "meta" in body

    async def test_populated_table_returns_source_rows(self):
        """Two source adapters → two rows in response, sorted by source_name ASC."""
        adapter_rows = [
            _row(
                {
                    "source_name": "core.sessions",
                    "chronicler_compatibility": "supported",
                    "read_surface": "sessions_table",
                    "boundary_semantics": "wall_clock",
                    "optional_schema": False,
                    "active": True,
                    "inactive_reason": None,
                }
            ),
            _row(
                {
                    "source_name": "spotify.session_summary",
                    "chronicler_compatibility": "supported",
                    "read_surface": "api",
                    "boundary_semantics": "wall_clock",
                    "optional_schema": True,
                    "active": False,
                    "inactive_reason": "Missing spotify schema",
                }
            ),
        ]
        checkpoint_rows: list[_Row] = []  # no checkpoint data
        app, _ = _make_app(fetch_side_effect=[adapter_rows, checkpoint_rows])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["source_name"] == "core.sessions"
        assert data[1]["source_name"] == "spotify.session_summary"
        assert data[1]["active"] is False
        assert data[1]["inactive_reason"] == "Missing spotify schema"

    async def test_checkpoint_aggregation_latest_run_at(self):
        """last_run_at is the max across all subsources for a given source."""
        t_old = datetime(2026, 4, 24, 10, 0, 0, tzinfo=UTC)
        t_new = datetime(2026, 4, 25, 8, 0, 0, tzinfo=UTC)

        adapter_rows = [
            _row(
                {
                    "source_name": "core.sessions",
                    "chronicler_compatibility": "supported",
                    "read_surface": "sessions",
                    "boundary_semantics": "wall_clock",
                    "optional_schema": False,
                    "active": True,
                    "inactive_reason": None,
                }
            ),
        ]
        checkpoint_rows = [
            _row(
                {
                    "source_name": "core.sessions",
                    "subsource": "butler_a",
                    "last_run_at": t_old,
                    "last_error": None,
                }
            ),
            _row(
                {
                    "source_name": "core.sessions",
                    "subsource": "butler_b",
                    "last_run_at": t_new,
                    "last_error": "timeout",
                }
            ),
        ]
        app, _ = _make_app(fetch_side_effect=[adapter_rows, checkpoint_rows])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        # last_run_at must be the newer timestamp
        assert row["last_run_at"] is not None
        assert row["last_run_at"].startswith("2026-04-25")
        assert row["last_error"] == "timeout"

    async def test_subsource_checkpoints_included(self):
        """subsource_checkpoints array carries per-subsource detail."""
        t1 = datetime(2026, 4, 24, 9, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 4, 25, 7, 0, 0, tzinfo=UTC)

        adapter_rows = [
            _row(
                {
                    "source_name": "core.sessions",
                    "chronicler_compatibility": "supported",
                    "read_surface": "sessions",
                    "boundary_semantics": "wall_clock",
                    "optional_schema": False,
                    "active": True,
                    "inactive_reason": None,
                }
            ),
        ]
        checkpoint_rows = [
            _row(
                {
                    "source_name": "core.sessions",
                    "subsource": "schema_a",
                    "last_run_at": t1,
                    "last_error": None,
                }
            ),
            _row(
                {
                    "source_name": "core.sessions",
                    "subsource": "schema_b",
                    "last_run_at": t2,
                    "last_error": "db error",
                }
            ),
        ]
        app, _ = _make_app(fetch_side_effect=[adapter_rows, checkpoint_rows])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        checkpoints = row["subsource_checkpoints"]
        assert checkpoints is not None
        assert len(checkpoints) == 2
        subsources = {cp["subsource"] for cp in checkpoints}
        assert subsources == {"schema_a", "schema_b"}

    async def test_meals_source_row_returned(self):
        """health.meals PLANNED source row is returned with correct compatibility."""
        adapter_rows = [
            _row(
                {
                    "source_name": "health.meals",
                    "chronicler_compatibility": "planned",
                    "read_surface": None,
                    "boundary_semantics": "eating_event point events; one row per logged meal",
                    "optional_schema": True,
                    "active": False,
                    "inactive_reason": None,
                }
            ),
        ]
        app, _ = _make_app(fetch_side_effect=[adapter_rows, []])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        row = data[0]
        assert row["source_name"] == "health.meals"
        assert row["chronicler_compatibility"] == "planned"
        assert row["optional_schema"] is True

    async def test_no_checkpoints_returns_null_subsource_checkpoints(self):
        """Source with no checkpoint rows → subsource_checkpoints is null."""
        adapter_rows = [
            _row(
                {
                    "source_name": "steam.play_history",
                    "chronicler_compatibility": "planned",
                    "read_surface": None,
                    "boundary_semantics": None,
                    "optional_schema": False,
                    "active": False,
                    "inactive_reason": None,
                }
            ),
        ]
        app, _ = _make_app(fetch_side_effect=[adapter_rows, []])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert row["subsource_checkpoints"] is None
        assert row["last_run_at"] is None
        assert row["last_error"] is None


class TestSourceStateMethodNotAllowed:
    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    async def test_non_get_returns_405(self, method: str):
        """Any HTTP method other than GET must return 405."""
        app, _ = _make_app(fetch_side_effect=[[], []])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await getattr(client, method)("/api/chronicler/source-state")
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# Guardrail: all SQL in the handler references only chronicler.* relations
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
        # Core butler tables present in every butler schema:
        "scheduled_tasks",
        # Per-butler table accessed via fan_out (ops escape hatch, not chronicler pool):
        "sessions",
    }
)


def _extract_sql_strings(source: str) -> list[str]:
    """Return all string literals from Python source that look like SQL statements.

    Only strings that begin (after stripping) with a SQL DML/DDL keyword are
    considered SQL, so docstrings and log messages that happen to contain
    words like FROM or JOIN are excluded.
    """
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


_SQL_NON_RELATION_KEYWORDS = frozenset(
    {
        "lateral",
        "only",
        "inner",
        "outer",
        "left",
        "right",
        "full",
        "cross",
        "natural",
        "select",
        "values",
        "with",
    }
)


def _extract_relation_names(sql: str) -> list[str]:
    """Very lightweight extraction: words after FROM or JOIN keywords."""
    import re

    # Capture identifiers after FROM / JOIN (optional schema prefix stripped)
    tokens = re.findall(r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", sql, re.IGNORECASE)
    relations = []
    for tok in tokens:
        # Skip SQL keywords that are not relation names (e.g. LATERAL in
        # "CROSS JOIN LATERAL <function>(...)").
        if tok.lower() in _SQL_NON_RELATION_KEYWORDS:
            continue
        # Strip schema prefix if present (e.g. chronicler.episodes → episodes)
        bare = tok.split(".")[-1].lower().strip()
        relations.append(bare)
    return relations


def test_source_state_sql_only_uses_chronicler_relations():
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
