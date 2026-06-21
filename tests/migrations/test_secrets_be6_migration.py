"""Tests for core_107 secrets BE-6 migration.

Covers:
- Revision chain integrity (core_107 → core_106)
- public.provider_feature_catalogue table structure (columns, types, constraints)
- UNIQUE constraint on (provider, butler, feature)
- Index ix_provider_feature_catalogue_provider_butler on (provider, butler)
- Seed rows present in upgrade source for all 7 known providers
- ON CONFLICT DO NOTHING idempotency in seed block
- Downgrade drops the table and its index
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
    / "core_107_provider_feature_catalogue.py"
)

_KNOWN_PROVIDERS = (
    "google",
    "telegram",
    "spotify",
    "home_assistant",
    "whatsapp",
    "owntracks",
    "steam",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_107", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_107"
    assert mod.down_revision == "core_106"


def test_table_structure():
    """provider_feature_catalogue has the documented columns/types (required_scopes JSONB)."""
    source = _MIGRATION_PATH.read_text()
    assert "public.provider_feature_catalogue" in source
    assert "BIGSERIAL" in source
    assert "PRIMARY KEY" in source
    for col in ("provider", "butler", "feature", "severity", "required_scopes", "updated_at"):
        assert col in source, f"Column '{col}' missing from migration source"
    assert "JSONB" in source  # required_scopes
    assert "TIMESTAMPTZ" in source  # updated_at
    assert "::jsonb" in source  # required_scopes seed values cast to JSONB


def test_severity_check_constraint():
    """severity must be constrained to 'high', 'medium', 'low' (covers all seed severities)."""
    source = _MIGRATION_PATH.read_text()
    assert "CHECK" in source
    assert "'high'" in source
    assert "'medium'" in source
    assert "'low'" in source


def test_unique_constraint_on_provider_butler_feature():
    """Unique constraint (provider, butler, feature) present."""
    source = _MIGRATION_PATH.read_text()
    assert "UNIQUE" in source
    assert "provider, butler, feature" in source


def test_index_on_provider_butler():
    """Index ix_provider_feature_catalogue_provider_butler on (provider, butler)."""
    source = _MIGRATION_PATH.read_text()
    assert "ix_provider_feature_catalogue_provider_butler" in source
    assert "(provider, butler)" in source


def test_seed_contains_all_seven_known_providers():
    """All 7 known providers appear in the upgrade seed block."""
    source = _MIGRATION_PATH.read_text()
    for provider in _KNOWN_PROVIDERS:
        assert f"'{provider}'" in source, f"Provider '{provider}' missing from migration seed"


def test_seed_uses_on_conflict_do_nothing():
    """Seed INSERT is idempotent via ON CONFLICT DO NOTHING."""
    source = _MIGRATION_PATH.read_text()
    assert "ON CONFLICT" in source
    assert "DO NOTHING" in source


def test_downgrade_drops_table_and_index():
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS public.provider_feature_catalogue" in source
    assert "DROP INDEX IF EXISTS public.ix_provider_feature_catalogue_provider_butler" in source


def test_table_grants_cover_all_runtime_roles():
    """Migration grants SELECT/INSERT/UPDATE to all runtime roles."""
    source = _MIGRATION_PATH.read_text()
    # The _ALL_RUNTIME_ROLES list in the migration must include canonical roles.
    for role_fragment in (
        "butler_health_rw",
        "butler_lifestyle_rw",
        "butler_messenger_rw",
        "butler_home_rw",
    ):
        assert role_fragment in source, (
            f"Runtime role '{role_fragment}' missing from migration grants"
        )
