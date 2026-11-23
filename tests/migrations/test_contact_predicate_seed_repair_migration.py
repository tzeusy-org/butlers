"""Tests for rel_017 predicate seed repair migration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "relationship"
    / "migrations"
    / "017_repair_contact_predicate_seeds.py"
)


def _collect_upgrade_sqls() -> list[str]:
    mod = load_migration(_MIGRATION_PATH, "rel_017")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


def test_upgrade_upserts_has_handle_predicate() -> None:
    sqls = _collect_upgrade_sqls()
    joined = "\n".join(sqls)
    assert "relationship.entity_predicate_registry" in joined
    assert "'has-handle'" in joined
    assert "ON CONFLICT (predicate) DO UPDATE" in joined
    assert "object_kind = EXCLUDED.object_kind" in joined


def test_upgrade_repairs_all_rel014_predicates() -> None:
    sqls = _collect_upgrade_sqls()
    joined = "\n".join(sqls)
    for predicate in (
        "has-email",
        "has-phone",
        "has-handle",
        "has-address",
        "has-birthday",
        "has-website",
        "knows",
        "family-of",
        "partner-of",
        "parent-of",
        "child-of",
        "colleague-of",
        "friend-of",
        "co-attended",
        "purchased-from",
        "subscribed-to",
        "visited",
        "dunbar_tier_override",
    ):
        assert f"'{predicate}'" in joined


def test_upgrade_refreshes_predicate_metadata() -> None:
    sqls = _collect_upgrade_sqls()
    joined = "\n".join(sqls)
    assert "kind = EXCLUDED.kind" in joined
    assert "description = EXCLUDED.description" in joined
    assert "Object is a JSON number (1-5)." in joined


def test_downgrade_is_noop() -> None:
    mod = load_migration(_MIGRATION_PATH, "rel_017")
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        assert mod.downgrade() is None
    mock_op.execute.assert_not_called()
