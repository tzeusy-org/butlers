"""Tests for the degraded-source envelope on GET /api/issues (bu-tpudw.3).

This surface's whole product IS failure, so a query error on either DB-backed
source (audit-groups or acks) must be surfaced via ``meta.sources_degraded``
rather than silently zero-filling into an all-clear empty feed (CLAUDE.md
Degraded-Mode Response Envelope). These tests exercise all three directions:

- degraded (a genuine query error) -> the source is named in the meta flag;
- legitimately-absent (a pre-migration missing table) -> NOT flagged;
- healthy-empty -> the honest all-clear (flag absent/empty).

Mutation strength: the degraded assertions read the flag itself, so a change
that drops the flag (reverting to the old bare ``except: return []``) fails.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asyncpg.exceptions import UndefinedTableError

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_mcp_manager
from butlers.api.routers.issues import (
    _SOURCE_ACKS,
    _SOURCE_AUDIT_GROUPS,
    _get_db_manager,
)

pytestmark = pytest.mark.unit


def _is_audit_query(sql: str) -> bool:
    return "grouped_errors" in sql


def _is_acks_query(sql: str) -> bool:
    return "dismissed_issues" in sql


def _build_app(fetch_side_effect: Any) -> tuple[Any, MagicMock]:
    """Build the app with a switchboard pool whose ``fetch`` is scripted.

    ``fetch_side_effect`` is an async callable ``(sql, *args) -> rows`` (or a
    function that raises) so a test can fail one source while the other
    answers.
    """
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_butler_configs] = lambda: []
    return app, mock_pool


async def _call(app: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/issues")


class TestIssuesDegradedHealthyEmpty:
    async def test_both_sources_answer_empty_keeps_honest_all_clear(self) -> None:
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            return []

        app, _ = _build_app(fetch)

        resp = await _call(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        # Healthy-empty: no degraded flag, so the frontend renders its honest
        # all-clear empty state.
        assert "sources_degraded" not in body["meta"]


class TestIssuesDegradedGenuineFailure:
    async def test_audit_groups_failure_is_named(self) -> None:
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            if _is_audit_query(sql):
                raise ConnectionError("connection reset by peer")
            return []

        app, _ = _build_app(fetch)

        resp = await _call(app)

        assert resp.status_code == 200
        body = resp.json()
        # Mutation strength: assert the flag names the dropped source. If the
        # flag were ignored (old bare except), this list would be absent.
        assert body["meta"]["sources_degraded"] == [_SOURCE_AUDIT_GROUPS]

    async def test_acks_failure_is_named(self) -> None:
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            if _is_acks_query(sql):
                raise ConnectionError("connection reset by peer")
            return []

        app, _ = _build_app(fetch)

        resp = await _call(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["sources_degraded"] == [_SOURCE_ACKS]

    async def test_both_sources_failing_names_both(self) -> None:
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            raise ConnectionError("connection reset by peer")

        app, _ = _build_app(fetch)

        resp = await _call(app)

        assert resp.status_code == 200
        body = resp.json()
        degraded = body["meta"]["sources_degraded"]
        assert set(degraded) == {_SOURCE_AUDIT_GROUPS, _SOURCE_ACKS}


class TestIssuesDegradedLegitimatelyAbsent:
    async def test_missing_table_is_not_flagged(self) -> None:
        # A pre-migration table (UndefinedTableError) is legitimately absent,
        # not a degraded source -- classify-before-flagging. The feed is an
        # honest empty, so the flag stays absent.
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            raise UndefinedTableError('relation "public.dismissed_issues" does not exist')

        app, _ = _build_app(fetch)

        resp = await _call(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert "sources_degraded" not in body["meta"]

    async def test_missing_relation_message_is_not_flagged(self) -> None:
        # Same classification via message text (a driver that surfaces the
        # missing relation as a generic error rather than UndefinedTableError).
        async def fetch(sql: str, *_args: Any) -> list[Any]:
            raise RuntimeError('relation "public.audit_log" does not exist')

        app, _ = _build_app(fetch)

        resp = await _call(app)

        assert resp.status_code == 200
        body = resp.json()
        assert "sources_degraded" not in body["meta"]
