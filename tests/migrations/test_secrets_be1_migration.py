"""Tests for core_105 secrets BE-1 migration.

Covers:
- Revision chain integrity (core_105 → core_104)
- public.secret_probe_log table and ix_secret_probe_log_lookup index
- ix_audit_log_target_ts index on public.audit_log (target, ts DESC)
- Audit action vocabulary documentation in migration source
- Downgrade drops the table and both indexes
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
    / "core_105_secrets_be1.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_105", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_105"
    assert mod.down_revision == "core_104"


def test_upgrade_creates_secret_probe_log_table():
    source = _MIGRATION_PATH.read_text()
    assert "public.secret_probe_log" in source
    assert "BIGSERIAL" in source
    assert "credential_scope" in source
    assert "credential_key" in source
    assert "ok" in source
    assert "BOOLEAN" in source
    assert "latency_ms" in source
    assert "TIMESTAMPTZ" in source
    assert "recorded_at" in source
    assert "message" in source


def test_upgrade_creates_secret_probe_log_lookup_index():
    source = _MIGRATION_PATH.read_text()
    assert "ix_secret_probe_log_lookup" in source
    assert "credential_scope, credential_key, recorded_at DESC" in source


def test_upgrade_creates_audit_log_target_ts_index():
    source = _MIGRATION_PATH.read_text()
    assert "ix_audit_log_target_ts" in source
    assert "public.audit_log" in source
    assert "target, ts DESC" in source


def test_audit_credential_actions_documented():
    """Credential-lifecycle action vocabulary is documented in the migration."""
    source = _MIGRATION_PATH.read_text()
    for action in (
        "verified",
        "failed",
        "rotated",
        "connected",
        "disconnected",
        "warned",
        "overrode",
        "revoked",
        "attempted",
        "set",
    ):
        assert action in source, f"Action '{action}' missing from migration source"


def test_downgrade_drops_table_and_both_indexes():
    source = _MIGRATION_PATH.read_text()
    assert "DROP TABLE IF EXISTS public.secret_probe_log" in source
    assert "DROP INDEX IF EXISTS public.ix_secret_probe_log_lookup" in source
    assert "DROP INDEX IF EXISTS public.ix_audit_log_target_ts" in source
