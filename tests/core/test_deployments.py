"""Unit tests for butlers.core.deployments (bu-9r3hd.2).

Mirrors the AsyncMock-pool style used by tests/core/test_delegation_ledger.py --
the real-Postgres round trip lives in tests/integration/test_deployments_roundtrip.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.core.deployments import (
    VALID_RESULTS,
    get_current_deployment,
    list_recent_deployments,
    read_migration_head,
    record_deployment,
    resolve_git_sha,
)

pytestmark = pytest.mark.unit


class TestResolveGitSha:
    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "abc1234")
        assert resolve_git_sha() == "abc1234"

    def test_falls_back_to_unknown_when_unset(self, monkeypatch):
        monkeypatch.delenv("GIT_SHA", raising=False)
        assert resolve_git_sha() == "unknown"

    def test_falls_back_to_unknown_when_empty(self, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "")
        assert resolve_git_sha() == "unknown"


class TestReadMigrationHead:
    """bu-hmdqz.1: the schema's alembic_version table legitimately holds one
    row per independent chain ever applied there (core, memory, ...), so
    read_migration_head must isolate the core chain's row rather than
    grabbing an arbitrary one (the observed bug: a stale 'mem_007' row
    surfacing on /system instead of a core_NNN head)."""

    async def test_returns_head_value_when_only_core_applied(self, monkeypatch):
        monkeypatch.setattr(
            "butlers.core.deployments.get_chain_revision_ids",
            lambda chain: frozenset({"core_163"}) if chain == "core" else frozenset(),
        )
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"version_num": "core_163"}])
        result = await read_migration_head(pool, "switchboard")
        assert result == "core_163"
        query = pool.fetch.await_args.args[0]
        assert '"switchboard".alembic_version' in query
        assert "LIMIT" not in query

    async def test_filters_out_non_core_rows(self, monkeypatch):
        """A schema carrying both a stale module row and the core row must
        report only the core one -- not whichever Postgres returns first."""
        monkeypatch.setattr(
            "butlers.core.deployments.get_chain_revision_ids",
            lambda chain: frozenset({"core_163"}) if chain == "core" else frozenset(),
        )
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[{"version_num": "mem_007"}, {"version_num": "core_163"}]
        )
        result = await read_migration_head(pool, "public")
        assert result == "core_163"

    async def test_returns_none_when_core_chain_never_applied(self, monkeypatch):
        monkeypatch.setattr(
            "butlers.core.deployments.get_chain_revision_ids",
            lambda chain: frozenset({"core_163"}) if chain == "core" else frozenset(),
        )
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"version_num": "mem_007"}])
        result = await read_migration_head(pool, "public")
        assert result is None

    async def test_query_failure_returns_none_not_raise(self):
        """A missing/unreadable alembic_version table must not fail the boot."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("relation does not exist"))
        result = await read_migration_head(pool, "switchboard")
        assert result is None


class TestRecordDeployment:
    async def test_rejects_invalid_result(self):
        pool = AsyncMock()
        with pytest.raises(ValueError):
            await record_deployment(
                pool, git_sha="abc1234", migration_head="core_163", result="in_progress"
            )
        pool.fetchval.assert_not_awaited()

    async def test_insert_returns_row_id(self):
        row_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=row_id)

        result = await record_deployment(
            pool, git_sha="abc1234", migration_head="core_163", result="success"
        )
        assert result == str(row_id)

        pool.fetchval.assert_awaited_once()
        query, *params = pool.fetchval.await_args.args
        assert "INSERT INTO public.deployments" in query
        assert params == ["abc1234", "core_163", "success"]

    async def test_allows_null_migration_head(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=uuid.uuid4())
        await record_deployment(pool, git_sha="abc1234", migration_head=None, result="failed")
        _query, *params = pool.fetchval.await_args.args
        assert params[1] is None

    async def test_db_error_propagates(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=Exception("connection reset"))
        with pytest.raises(Exception, match="connection reset"):
            await record_deployment(
                pool, git_sha="abc1234", migration_head="core_163", result="success"
            )

    def test_valid_results_matches_migration_check_constraint(self):
        assert VALID_RESULTS == {"success", "failed"}


class TestGetCurrentDeployment:
    async def test_returns_none_when_empty(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        assert await get_current_deployment(pool) is None

    async def test_returns_dict_of_latest_row(self):
        pool = AsyncMock()
        row_id = uuid.uuid4()
        pool.fetchrow = AsyncMock(return_value={"id": row_id, "result": "success"})
        result = await get_current_deployment(pool)
        assert result == {"id": row_id, "result": "success"}


class TestListRecentDeployments:
    async def test_returns_rows_in_order(self):
        pool = AsyncMock()
        rows = [{"id": uuid.uuid4(), "result": "success"} for _ in range(3)]
        pool.fetch = AsyncMock(return_value=rows)
        result = await list_recent_deployments(pool, limit=5)
        assert result == rows
        query, limit = pool.fetch.await_args.args
        assert "ORDER BY started_at DESC" in query
        assert limit == 5

    async def test_default_limit_is_ten(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        await list_recent_deployments(pool)
        _query, limit = pool.fetch.await_args.args
        assert limit == 10
