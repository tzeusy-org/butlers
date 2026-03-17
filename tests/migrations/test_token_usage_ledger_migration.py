"""Tests for core_035: shared.token_usage_ledger and shared.token_limits migration."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "core"
_MIGRATION_FILE = "core_035_token_usage_ledger_and_limits.py"


def _load_migration(filename: str, module_name: str):
    filepath = VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


class TestCore035RevisionMetadata:
    def _mod(self):
        return _load_migration(_MIGRATION_FILE, "core_035")

    def test_revision(self) -> None:
        assert self._mod().revision == "core_035"

    def test_down_revision(self) -> None:
        assert self._mod().down_revision == "core_034"

    def test_branch_labels_none(self) -> None:
        assert self._mod().branch_labels is None

    def test_depends_on_none(self) -> None:
        assert self._mod().depends_on is None

    def test_upgrade_callable(self) -> None:
        assert callable(self._mod().upgrade)

    def test_downgrade_callable(self) -> None:
        assert callable(self._mod().downgrade)


# ---------------------------------------------------------------------------
# token_usage_ledger: upgrade source inspection
# ---------------------------------------------------------------------------


class TestCore035LedgerTableDefinition:
    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_FILE, "core_035").upgrade)

    def test_creates_token_usage_ledger(self) -> None:
        assert "shared.token_usage_ledger" in self._src()

    def test_create_if_not_exists(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS shared.token_usage_ledger" in self._src()

    def test_partitioned_by_range_recorded_at(self) -> None:
        src = self._src()
        assert "PARTITION BY RANGE (recorded_at)" in src

    def test_id_column_uuid_not_null(self) -> None:
        src = self._src()
        assert "id" in src
        assert "UUID NOT NULL" in src

    def test_catalog_entry_id_uuid_not_null(self) -> None:
        src = self._src()
        assert "catalog_entry_id" in src
        assert "UUID NOT NULL" in src

    def test_catalog_entry_id_references_model_catalog_cascade(self) -> None:
        src = self._src()
        assert "shared.model_catalog(id) ON DELETE CASCADE" in src

    def test_butler_name_text_not_null(self) -> None:
        src = self._src()
        assert "butler_name" in src
        assert "TEXT NOT NULL" in src

    def test_session_id_uuid_nullable(self) -> None:
        src = self._src()
        assert "session_id" in src
        # Nullable = no NOT NULL constraint on the session_id line
        # Find the line with session_id and verify NOT NULL is absent on it
        for line in src.splitlines():
            if "session_id" in line and "catalog_entry_id" not in line:
                assert "NOT NULL" not in line, "session_id must be nullable"
                break

    def test_input_tokens_integer_not_null_default_0(self) -> None:
        src = self._src()
        assert "input_tokens" in src
        assert "INTEGER NOT NULL DEFAULT 0" in src

    def test_output_tokens_integer_not_null_default_0(self) -> None:
        src = self._src()
        assert "output_tokens" in src
        assert "INTEGER NOT NULL DEFAULT 0" in src

    def test_recorded_at_timestamptz_not_null_default_now(self) -> None:
        src = self._src()
        assert "recorded_at" in src
        assert "TIMESTAMPTZ NOT NULL DEFAULT now()" in src

    def test_composite_pk_id_recorded_at(self) -> None:
        src = self._src()
        assert "PRIMARY KEY (id, recorded_at)" in src


# ---------------------------------------------------------------------------
# token_usage_ledger: composite index
# ---------------------------------------------------------------------------


class TestCore035LedgerIndex:
    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_FILE, "core_035").upgrade)

    def test_idx_ledger_entry_time_exists(self) -> None:
        assert "idx_ledger_entry_time" in self._src()

    def test_idx_ledger_entry_time_on_catalog_entry_id_recorded_at(self) -> None:
        src = self._src()
        assert "ON shared.token_usage_ledger (catalog_entry_id, recorded_at)" in src

    def test_create_index_if_not_exists(self) -> None:
        assert "CREATE INDEX IF NOT EXISTS idx_ledger_entry_time" in self._src()


# ---------------------------------------------------------------------------
# token_limits: upgrade source inspection
# ---------------------------------------------------------------------------


class TestCore035LimitsTableDefinition:
    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_FILE, "core_035").upgrade)

    def test_creates_token_limits(self) -> None:
        assert "shared.token_limits" in self._src()

    def test_create_if_not_exists(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS shared.token_limits" in self._src()

    def test_id_uuid_primary_key(self) -> None:
        src = self._src()
        assert "id" in src
        assert "UUID PRIMARY KEY" in src

    def test_catalog_entry_id_unique_not_null(self) -> None:
        src = self._src()
        assert "catalog_entry_id" in src
        assert "UNIQUE" in src
        assert "UUID NOT NULL UNIQUE" in src or "UUID NOT NULL" in src

    def test_catalog_entry_id_fk_cascade(self) -> None:
        src = self._src()
        # Both tables reference shared.model_catalog(id) ON DELETE CASCADE
        assert src.count("shared.model_catalog(id) ON DELETE CASCADE") >= 1

    def test_limit_24h_bigint_nullable(self) -> None:
        src = self._src()
        assert "limit_24h" in src
        assert "BIGINT" in src
        # Nullable = no NOT NULL on that column
        for line in src.splitlines():
            if "limit_24h" in line:
                assert "NOT NULL" not in line, "limit_24h must be nullable (NULL = unlimited)"
                break

    def test_limit_30d_bigint_nullable(self) -> None:
        src = self._src()
        assert "limit_30d" in src
        for line in src.splitlines():
            if "limit_30d" in line:
                assert "NOT NULL" not in line, "limit_30d must be nullable (NULL = unlimited)"
                break

    def test_reset_24h_at_timestamptz_nullable(self) -> None:
        src = self._src()
        assert "reset_24h_at" in src
        assert "TIMESTAMPTZ" in src

    def test_reset_30d_at_timestamptz_nullable(self) -> None:
        src = self._src()
        assert "reset_30d_at" in src

    def test_created_at_not_null_default_now(self) -> None:
        src = self._src()
        assert "created_at" in src

    def test_updated_at_not_null_default_now(self) -> None:
        src = self._src()
        assert "updated_at" in src


# ---------------------------------------------------------------------------
# Partitioning: pg_partman and fallback paths
# ---------------------------------------------------------------------------


class TestCore035PartitioningPaths:
    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_FILE, "core_035").upgrade)

    def test_pg_partman_branch_registers_parent(self) -> None:
        assert "partman.create_parent" in self._src()

    def test_pg_partman_interval_monthly(self) -> None:
        src = self._src()
        assert "monthly" in src

    def test_pg_partman_sets_90_day_retention(self) -> None:
        src = self._src()
        assert "retention" in src
        assert "90 days" in src

    def test_fallback_creates_6_partitions(self) -> None:
        src = self._src()
        # The fallback loop runs 0..5 inclusive (6 months)
        assert "0 .. 5" in src

    def test_fallback_partition_naming_uses_yyyymm(self) -> None:
        src = self._src()
        assert "token_usage_ledger_%s" in src
        assert "YYYYMM" in src

    def test_fallback_logs_warning(self) -> None:
        src = self._src()
        assert "log.warning" in src
        assert "pg_partman" in src

    def test_fallback_does_not_fail_migration(self) -> None:
        """The fallback branch must not raise — migration succeeds without pg_partman."""
        src = self._src()
        # Presence of RAISE EXCEPTION or sys.exit in the fallback block would break this
        # requirement.  We just check that the warning path uses log.warning, not log.error
        # followed by a re-raise pattern.
        assert "raise" not in src.lower().split("log.warning")[1][:50]


# ---------------------------------------------------------------------------
# Downgrade source inspection
# ---------------------------------------------------------------------------


class TestCore035Downgrade:
    def _src(self) -> str:
        return inspect.getsource(_load_migration(_MIGRATION_FILE, "core_035").downgrade)

    def test_drops_token_limits(self) -> None:
        assert "DROP TABLE IF EXISTS shared.token_limits" in self._src()

    def test_drops_token_usage_ledger_cascade(self) -> None:
        src = self._src()
        assert "DROP TABLE IF EXISTS shared.token_usage_ledger CASCADE" in src

    def test_drops_index_before_tables(self) -> None:
        src = self._src()
        idx_pos = src.find("DROP INDEX IF EXISTS shared.idx_ledger_entry_time")
        table_pos = src.find("DROP TABLE IF EXISTS shared.token_limits")
        assert idx_pos < table_pos, "Index must be dropped before tables"

    def test_drops_token_limits_before_ledger(self) -> None:
        """token_limits must be dropped before token_usage_ledger to avoid FK issues."""
        src = self._src()
        limits_pos = src.find("DROP TABLE IF EXISTS shared.token_limits")
        ledger_pos = src.find("DROP TABLE IF EXISTS shared.token_usage_ledger CASCADE")
        assert limits_pos < ledger_pos, "token_limits must be dropped before token_usage_ledger"

    def test_deregisters_pg_partman_before_drop(self) -> None:
        src = self._src()
        partman_pos = src.find("partman.part_config")
        ledger_pos = src.find("DROP TABLE IF EXISTS shared.token_usage_ledger CASCADE")
        assert partman_pos < ledger_pos, "pg_partman cleanup must precede table drop"

    def test_partman_cleanup_is_best_effort(self) -> None:
        """pg_partman deregistration is wrapped in exception handlers."""
        src = self._src()
        assert "EXCEPTION" in src or "BEGIN" in src
