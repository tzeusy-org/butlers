"""Unit tests for current relationship root migration (rel_001)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "001_relationship_tables.py"


def _relationship_migration_dir() -> Path:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("relationship")
    assert chain_dir is not None
    return chain_dir


def _load_migration():
    migration_path = _relationship_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Missing migration file: {MIGRATION_FILENAME}"
    spec = importlib.util.spec_from_file_location("rel_001_relationship_tables", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata() -> None:
    mod = _load_migration()
    assert mod.revision == "rel_001"
    assert mod.down_revision is None
    assert mod.branch_labels == ("relationship",)


def test_upgrade_creates_contacts_and_relationships() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert "CREATE TABLE IF NOT EXISTS contacts" in source
    assert "CREATE TABLE IF NOT EXISTS relationships" in source
    assert "REFERENCES contacts(id) ON DELETE CASCADE" in source


def test_upgrade_creates_reminders_and_activity_indexes() -> None:
    source = inspect.getsource(_load_migration().upgrade)
    assert "CREATE TABLE IF NOT EXISTS reminders" in source
    assert "idx_interactions_contact_occurred" in source
    assert "idx_activity_feed_contact_created" in source


def test_downgrade_drops_core_relationship_tables() -> None:
    source = inspect.getsource(_load_migration().downgrade)
    assert "DROP TABLE IF EXISTS relationships" in source
    assert "DROP TABLE IF EXISTS contacts" in source
