"""Unit tests for backfill_jobs in consolidated switchboard email migration (sw_004)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_FILENAME = "004_switchboard_email.py"


def _migration_file() -> Path:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("switchboard")
    assert chain_dir is not None
    return chain_dir / _MIGRATION_FILENAME


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_004_switchboard_email", _migration_file())
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_sql() -> str:
    return inspect.getsource(_load_migration().upgrade)


def _downgrade_sql() -> str:
    return inspect.getsource(_load_migration().downgrade)


def test_revision_metadata() -> None:
    mod = _load_migration()
    assert mod.revision == "sw_004"
    assert mod.down_revision == "sw_003"


def test_upgrade_creates_backfill_jobs_table_and_columns() -> None:
    src = _upgrade_sql()
    assert "CREATE TABLE backfill_jobs" in src
    for col in (
        "connector_type",
        "endpoint_identity",
        "target_categories",
        "date_from",
        "date_to",
        "status",
        "cursor",
        "rows_processed",
        "rows_skipped",
        "cost_spent_cents",
        "daily_cost_cap_cents",
        "rate_limit_per_hour",
    ):
        assert col in src


def test_upgrade_creates_status_constraint_and_indexes() -> None:
    src = _upgrade_sql()
    assert "backfill_jobs_status_check" in src
    assert "idx_backfill_jobs_status" in src
    assert "idx_backfill_jobs_connector" in src


def test_downgrade_drops_backfill_jobs_and_indexes() -> None:
    src = _downgrade_sql()
    assert "DROP INDEX IF EXISTS idx_backfill_jobs_connector" in src
    assert "DROP INDEX IF EXISTS idx_backfill_jobs_status" in src
    assert "DROP TABLE IF EXISTS backfill_jobs" in src
