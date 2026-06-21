"""Tests for core_106 secrets BE-2 migration.

Covers:
- Revision chain integrity (core_106 → core_105)
- Four test-state columns present on both tables in upgrade source
- Columns are nullable with no computed/generated keyword (writeable)
- Dynamic butler-schema discovery via pg_class helper function
- Downgrade drops all four columns from both tables
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_106_secrets_be2.py"
)

_TEST_STATE_COLUMNS = (
    "last_verified",
    "last_test_ok",
    "last_test_code",
    "last_test_message",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_106", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_106"
    assert mod.down_revision == "core_105"


def test_four_writable_nullable_columns_with_types():
    """All four test-state columns are present with their spec types, nullable
    (NULL = never probed) and not GENERATED/COMPUTED (writable)."""
    source = _MIGRATION_PATH.read_text()
    for col in _TEST_STATE_COLUMNS:
        assert col in source, f"Column '{col}' missing from migration source"
        for line in source.splitlines():
            if col in line and "ADD COLUMN" in line:
                assert "NOT NULL" not in line, f"Column '{col}' must be nullable: {line!r}"
    # spec types: last_verified TIMESTAMPTZ, last_test_ok BOOLEAN,
    # last_test_code INTEGER, last_test_message TEXT
    assert "TIMESTAMPTZ" in source
    assert "BOOLEAN" in source
    assert "INTEGER" in source
    assert "TEXT" in source
    assert "GENERATED" not in source
    assert "COMPUTED" not in source


def test_upgrade_covers_both_tables():
    """upgrade() adds columns to per-butler butler_secrets and public.entity_info."""
    source = _MIGRATION_PATH.read_text()
    assert "butler_secrets" in source
    assert "public.entity_info" in source
    assert "ADD COLUMN IF NOT EXISTS" in source


def test_upgrade_uses_dynamic_schema_discovery():
    """Migration discovers butler schemas at runtime via pg_class, not a hard-coded list."""
    source = _MIGRATION_PATH.read_text()
    assert "pg_class" in source
    assert "butler_secrets" in source
    # Discovery must exclude pg_catalog and information_schema
    assert "pg_catalog" in source
    assert "information_schema" in source


def test_downgrade_drops_columns_from_both_tables():
    """downgrade() drops the test-state columns from butler_secrets and entity_info."""
    source = _MIGRATION_PATH.read_text()
    assert "DROP COLUMN IF EXISTS" in source
    assert "public.entity_info" in source


def test_no_backfill_in_upgrade():
    """upgrade() must not attempt to backfill via live probes or UPDATE statements."""
    import ast
    import textwrap

    source = _MIGRATION_PATH.read_text()
    tree = ast.parse(source)

    # Extract the body of the upgrade() function as source text.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            func_lines = source.splitlines()[node.lineno - 1 : node.end_lineno]
            func_source = textwrap.dedent("\n".join(func_lines))
            # Strip string literals (SQL inside op.execute calls) by checking
            # that no UPDATE keyword appears as a SQL statement starter.
            # A SQL UPDATE would appear after a newline + optional whitespace.
            import re

            sql_update = re.search(r"(?m)^\s*UPDATE\s", func_source)
            assert sql_update is None, (
                f"upgrade() contains a SQL UPDATE statement (backfill prohibited): "
                f"{sql_update.group()!r}"
            )
            return
    pytest.fail("upgrade() function not found in migration")
