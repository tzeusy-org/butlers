"""Tests for core_138 calendar_events pg_trgm search-index migration.

Static checks verify the migration file structure, revision chain, and SQL
content.  The integration test exercises the real upgrade against a migrated
database: the GIN trigram index exists, the upgrade SQL is re-runnable as a
no-op, and ``downgrade()`` drops the index while leaving the ``pg_trgm``
extension installed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    index_exists,
    migration_db_name,
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_138_calendar_events_search_trgm.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_138", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Static structure checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_migration_file_exists():
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


@pytest.mark.unit
def test_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_138"
    assert mod.down_revision == "core_137"
    assert mod.branch_labels is None


@pytest.mark.unit
def test_upgrade_ensures_extension_and_creates_trgm_index():
    source = _MIGRATION_PATH.read_text()
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in source
    assert "CREATE INDEX IF NOT EXISTS ix_calendar_events_search_trgm" in source
    assert "USING gin" in source
    # All three searchable columns are covered with gin_trgm_ops.
    for col in ("title gin_trgm_ops", "description gin_trgm_ops", "location gin_trgm_ops"):
        assert col in source, f"Missing trigram-covered column: {col}"


@pytest.mark.unit
def test_downgrade_drops_index_but_keeps_extension():
    source = _MIGRATION_PATH.read_text()
    assert "DROP INDEX IF EXISTS ix_calendar_events_search_trgm" in source
    # The shared extension must NOT be dropped on downgrade.
    assert "DROP EXTENSION" not in source


# ---------------------------------------------------------------------------
# Integration round-trip
# ---------------------------------------------------------------------------


def _extension_exists(db_url: str, extension_name: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = :n)"),
                {"n": extension_name},
            )
            return bool(result.scalar())
    finally:
        engine.dispose()


@pytest.mark.integration
def test_trgm_index_created_idempotent_and_downgrade_keeps_extension(postgres_container):
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    core = _build_alembic_config(db_url, chains=["core"])

    # Upgrade to core head (includes core_138).
    command.upgrade(core, "core@head")

    assert index_exists(db_url, "ix_calendar_events_search_trgm"), (
        "trigram index should exist after upgrade"
    )
    assert _extension_exists(db_url, "pg_trgm"), "pg_trgm extension should be installed"

    # Idempotency: re-running the upgrade SQL directly is a no-op (no error,
    # no duplicate index).
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_calendar_events_search_trgm "
                    "ON calendar_events USING gin ("
                    "  title gin_trgm_ops, description gin_trgm_ops, location gin_trgm_ops"
                    ")"
                )
            )
    finally:
        engine.dispose()
    assert index_exists(db_url, "ix_calendar_events_search_trgm")

    # Downgrade one step: index is dropped, extension stays.
    command.downgrade(core, "core_137")
    assert not index_exists(db_url, "ix_calendar_events_search_trgm"), (
        "trigram index should be dropped on downgrade"
    )
    assert _extension_exists(db_url, "pg_trgm"), (
        "downgrade must leave the shared pg_trgm extension installed"
    )
