"""Tests for rel_017 contact predicate seed repair migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "017_repair_contact_predicate_seeds.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("rel_017", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sqls() -> list[str]:
    mod = _load_migration()
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


def test_migration_file_exists() -> None:
    assert _MIGRATION_PATH.exists()


def test_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "rel_017"
    assert mod.down_revision == "rel_016"
    assert mod.branch_labels is None
    assert mod.depends_on is None


def test_upgrade_upserts_has_handle_predicate() -> None:
    sqls = _collect_upgrade_sqls()
    joined = "\n".join(sqls)
    assert "relationship.entity_predicate_registry" in joined
    assert "'has-handle'" in joined
    assert "ON CONFLICT (predicate) DO UPDATE" in joined
    assert "object_kind = EXCLUDED.object_kind" in joined


def test_upgrade_repairs_all_contact_predicates() -> None:
    sqls = _collect_upgrade_sqls()
    joined = "\n".join(sqls)
    for predicate in (
        "has-email",
        "has-phone",
        "has-handle",
        "has-address",
        "has-birthday",
        "has-website",
    ):
        assert f"'{predicate}'" in joined


def test_downgrade_is_noop() -> None:
    mod = _load_migration()
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        assert mod.downgrade() is None
    mock_op.execute.assert_not_called()
