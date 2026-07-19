"""Unit tests for butlers.core.deployments (bu-9r3hd.2).

Mirrors the AsyncMock-pool style used by tests/core/test_delegation_ledger.py --
the real-Postgres round trip lives in tests/integration/test_deployments_roundtrip.py.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.deployments import (
    VALID_DEPLOYMENT_SOURCES,
    VALID_RESULTS,
    VALID_SERVING_MODES,
    ServingProvenance,
    detect_boot_serving_provenance,
    get_current_deployment,
    list_recent_deployments,
    read_migration_head,
    record_deployment,
    resolve_core_migration_head,
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


class TestDetectBootServingProvenance:
    def test_reports_bind_mounted_worktree_from_mountinfo(self, tmp_path):
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "421 319 0:45 /home/tze/gt/butlers/.worktrees/frozen-checkout/src "
            "/app/src rw,relatime - ext4 /dev/sda rw\n"
        )

        provenance = detect_boot_serving_provenance(
            source_root=Path("/app/src"), mountinfo_path=mountinfo
        )

        assert provenance == ServingProvenance(
            serving_mode="hotreload-worktree",
            serving_worktree=".worktrees/frozen-checkout",
        )

    def test_reports_image_when_source_root_is_not_a_mount(self, tmp_path):
        source_root = tmp_path / "image-src"
        source_root.mkdir()
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text("24 1 0:22 / / rw,relatime - overlay overlay rw\n")

        provenance = detect_boot_serving_provenance(
            source_root=source_root, mountinfo_path=mountinfo
        )

        assert provenance == ServingProvenance(serving_mode="image", serving_worktree=None)

    def test_does_not_fabricate_image_when_a_source_mount_is_not_a_worktree(self, tmp_path):
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text(
            "421 319 0:45 /home/tze/gt/butlers/src /app/src rw,relatime - ext4 /dev/sda rw\n"
        )

        provenance = detect_boot_serving_provenance(
            source_root=Path("/app/src"), mountinfo_path=mountinfo
        )

        assert provenance == ServingProvenance(serving_mode=None, serving_worktree=None)

    def test_reports_unknown_when_runtime_source_root_is_absent(self, tmp_path):
        mountinfo = tmp_path / "mountinfo"
        mountinfo.write_text("")

        provenance = detect_boot_serving_provenance(
            source_root=tmp_path / "missing-src", mountinfo_path=mountinfo
        )

        assert provenance == ServingProvenance(serving_mode=None, serving_worktree=None)


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

    async def test_missing_table_is_expected_absent_no_traceback(self, caplog):
        """bu-l94um: an absent alembic_version table (e.g. ``public`` on the
        live DB) is legitimately-absent — return None WITHOUT a warning-level
        traceback. That stack-trace noise is what spooked the redeploy."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            side_effect=asyncpg.UndefinedTableError(
                'relation "public.alembic_version" does not exist'
            )
        )
        with caplog.at_level(logging.DEBUG, logger="butlers.core.deployments"):
            result = await read_migration_head(pool, "public")
        assert result is None
        # No WARNING (and therefore no exc_info traceback) for the benign case.
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(r.levelno == logging.DEBUG for r in caplog.records)

    async def test_genuine_failure_still_logs_loudly(self, caplog):
        """A non-missing-table failure (dropped connection, permission error)
        must still log at WARNING with a traceback."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=asyncpg.PostgresConnectionError("connection reset"))
        with caplog.at_level(logging.DEBUG, logger="butlers.core.deployments"):
            result = await read_migration_head(pool, "switchboard")
        assert result is None
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings and warnings[0].exc_info is not None


class TestResolveCoreMigrationHead:
    """bu-l94um: the core chain lives per-butler-schema, never in ``public``.
    resolve_core_migration_head must discover the schemas that actually track
    it via information_schema, not assume any single canonical schema."""

    @staticmethod
    def _make_pool(schemas: list[str], per_schema_rows: dict[str, list[str]]) -> AsyncMock:
        """Build a pool whose fetch() routes the pg_catalog schema-discovery
        query vs the per-schema alembic_version reads."""

        async def _fetch(query, *args):
            if "pg_class" in query:
                return [{"table_schema": s} for s in schemas]
            for schema, revs in per_schema_rows.items():
                if f'"{schema}".alembic_version' in query:
                    return [{"version_num": r} for r in revs]
            return []

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=_fetch)
        return pool

    @pytest.fixture(autouse=True)
    def _core_ids(self, monkeypatch):
        monkeypatch.setattr(
            "butlers.core.deployments.get_chain_revision_ids",
            lambda chain: (
                frozenset({"core_161", "core_162", "core_163"}) if chain == "core" else frozenset()
            ),
        )

    async def test_public_missing_butler_schema_present(self):
        """``public`` has no alembic_version; the butler schema carries the
        core head — the resolver reads it from where it actually lives."""
        pool = self._make_pool(
            schemas=["chronicler", "switchboard"],
            per_schema_rows={
                "chronicler": ["core_163", "mem_007"],
                "switchboard": ["core_163"],
            },
        )
        assert await resolve_core_migration_head(pool) == "core_163"

    async def test_no_alembic_anywhere_returns_none(self):
        """Nothing tracks the core chain (fresh DB) → None, no raise."""
        pool = self._make_pool(schemas=[], per_schema_rows={})
        assert await resolve_core_migration_head(pool) is None

    async def test_schemas_without_core_chain_return_none(self):
        """Schemas exist but only carry non-core chains → None."""
        pool = self._make_pool(
            schemas=["someschema"],
            per_schema_rows={"someschema": ["mem_007"]},
        )
        assert await resolve_core_migration_head(pool) is None

    async def test_divergent_heads_records_newest_and_warns(self, caplog):
        """When schemas disagree, record the newest head and log the
        divergence loudly — never silently smooth it over."""
        pool = self._make_pool(
            schemas=["chronicler", "switchboard"],
            per_schema_rows={
                "chronicler": ["core_161"],
                "switchboard": ["core_163"],
            },
        )
        with caplog.at_level(logging.WARNING, logger="butlers.core.deployments"):
            result = await resolve_core_migration_head(pool)
        assert result == "core_163"
        assert any("diverges across schemas" in r.message for r in caplog.records)


class TestRecordDeployment:
    async def test_rejects_invalid_result(self):
        pool = AsyncMock()
        with pytest.raises(ValueError):
            await record_deployment(
                pool,
                git_sha="abc1234",
                migration_head="core_163",
                result="in_progress",
                source="boot",
                serving_mode="image",
                serving_worktree=None,
            )
        pool.fetchval.assert_not_awaited()

    async def test_rejects_invalid_source(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="source"):
            await record_deployment(
                pool,
                git_sha="abc1234",
                migration_head="core_163",
                result="success",
                source="manual",
                serving_mode="image",
                serving_worktree=None,
            )
        pool.fetchval.assert_not_awaited()

    async def test_rejects_invalid_serving_mode(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="serving_mode"):
            await record_deployment(
                pool,
                git_sha="abc1234",
                migration_head="core_163",
                result="success",
                source="boot",
                serving_mode="container",
                serving_worktree=None,
            )
        pool.fetchval.assert_not_awaited()

    async def test_rejects_worktree_name_without_worktree_mode(self):
        pool = AsyncMock()
        with pytest.raises(ValueError, match="serving_worktree"):
            await record_deployment(
                pool,
                git_sha="abc1234",
                migration_head="core_163",
                result="success",
                source="deploy",
                serving_mode="image",
                serving_worktree=".worktrees/frozen-checkout",
            )
        pool.fetchval.assert_not_awaited()

    async def test_insert_returns_row_id(self):
        row_id = uuid.uuid4()
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=row_id)

        result = await record_deployment(
            pool,
            git_sha="abc1234",
            migration_head="core_163",
            result="success",
            source="boot",
            serving_mode="hotreload-worktree",
            serving_worktree=".worktrees/frozen-checkout",
        )
        assert result == str(row_id)

        pool.fetchval.assert_awaited_once()
        query, *params = pool.fetchval.await_args.args
        assert "INSERT INTO public.deployments" in query
        assert params == [
            "abc1234",
            "core_163",
            "success",
            "boot",
            "hotreload-worktree",
            ".worktrees/frozen-checkout",
        ]

    async def test_allows_null_migration_head(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=uuid.uuid4())
        await record_deployment(
            pool,
            git_sha="abc1234",
            migration_head=None,
            result="failed",
            source="boot",
            serving_mode=None,
            serving_worktree=None,
        )
        _query, *params = pool.fetchval.await_args.args
        assert params[1] is None

    async def test_db_error_propagates(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=Exception("connection reset"))
        with pytest.raises(Exception, match="connection reset"):
            await record_deployment(
                pool,
                git_sha="abc1234",
                migration_head="core_163",
                result="success",
                source="deploy",
                serving_mode="image",
                serving_worktree=None,
            )

    def test_valid_results_matches_migration_check_constraint(self):
        assert VALID_RESULTS == {"success", "failed"}

    def test_valid_provenance_vocabularies_match_migration_check_constraints(self):
        assert VALID_DEPLOYMENT_SOURCES == {"boot", "deploy"}
        assert VALID_SERVING_MODES == {"image", "hotreload-worktree"}


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
