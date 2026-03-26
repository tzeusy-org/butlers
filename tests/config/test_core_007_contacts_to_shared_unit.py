"""Unit tests for consolidated identity migration (core_002)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION_FILENAME = "core_002_identity.py"


def _core_migration_dir() -> Path:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir("core")
    assert chain_dir is not None, "Core chain should exist"
    return chain_dir


def _load_migration():
    migration_path = _core_migration_dir() / MIGRATION_FILENAME
    assert migration_path.exists(), f"Missing migration file: {MIGRATION_FILENAME}"
    spec = importlib.util.spec_from_file_location("core_002_identity", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata() -> None:
    mod = _load_migration()
    assert mod.revision == "core_002"
    assert mod.down_revision == "core_001"
    assert mod.branch_labels is None


def test_creates_identity_tables() -> None:
    src = inspect.getsource(_load_migration().upgrade)
    assert "CREATE TABLE IF NOT EXISTS public.entities" in src
    assert "CREATE TABLE IF NOT EXISTS public.contacts" in src
    assert "CREATE TABLE IF NOT EXISTS public.contact_info" in src
    assert "CREATE TABLE IF NOT EXISTS public.entity_info" in src


def test_owner_singleton_and_contact_uniques_present() -> None:
    src = inspect.getsource(_load_migration().upgrade)
    assert "ix_entities_owner_singleton" in src
    assert "uq_shared_contact_info_type_value" in src


def test_grants_loop_uses_all_butler_roles() -> None:
    src = inspect.getsource(_load_migration().upgrade)
    assert "_ALL_BUTLER_ROLES" in src
    assert "_grant_best_effort" in src


def test_downgrade_drops_identity_tables() -> None:
    src = inspect.getsource(_load_migration().downgrade)
    assert "DROP TABLE IF EXISTS public.entity_info CASCADE" in src
    assert "DROP TABLE IF EXISTS public.contact_info CASCADE" in src
    assert "DROP TABLE IF EXISTS public.contacts CASCADE" in src
    assert "DROP TABLE IF EXISTS public.entities CASCADE" in src
