"""Canonical migration contract tests for current consolidated chains."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class MigrationSpec:
    chain: str
    filename: str
    revision: str
    down_revision: str | None
    branch_labels: tuple[str, ...] | None
    depends_on: str | tuple[str, ...] | None = None

    @property
    def id(self) -> str:
        return f"{self.chain}/{self.filename}"


_MIGRATION_SPECS = [
    # Core consolidated chain
    MigrationSpec("core", "core_001_foundation.py", "core_001", None, ("core",)),
    MigrationSpec("core", "core_002_identity.py", "core_002", "core_001", None),
    MigrationSpec("core", "core_003_calendar.py", "core_003", "core_002", None),
    MigrationSpec("core", "core_004_model_and_tokens.py", "core_004", "core_003", None),
    MigrationSpec("core", "core_005_self_healing.py", "core_005", "core_004", None),
    MigrationSpec("core", "core_006_dashboard.py", "core_006", "core_005", None),
    MigrationSpec("core", "core_007_connectors.py", "core_007", "core_006", None),
    MigrationSpec("core", "core_008_external_accounts.py", "core_008", "core_007", None),
    MigrationSpec("core", "core_009_memory_catalog.py", "core_009", "core_008", None),
    # Switchboard consolidated chain
    MigrationSpec("switchboard", "001_switchboard_messaging.py", "sw_001", None, ("switchboard",)),
    MigrationSpec("switchboard", "002_switchboard_operations.py", "sw_002", "sw_001", None),
    MigrationSpec("switchboard", "003_switchboard_routing.py", "sw_003", "sw_002", None),
    MigrationSpec("switchboard", "004_switchboard_email.py", "sw_004", "sw_003", None),
    # Finance root
    MigrationSpec("finance", "001_finance_tables.py", "finance_001", None, ("finance",)),
]


def _load_migration_module(chain: str, filename: str):
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir(chain)
    assert chain_dir is not None, f"Chain {chain!r} should be resolvable"
    path = chain_dir / filename
    assert path.exists(), f"Migration file {chain}/{filename} should exist"

    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_file_exists(mspec: MigrationSpec) -> None:
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir(mspec.chain)
    assert chain_dir is not None, f"Chain {mspec.chain!r} should exist"
    assert (chain_dir / mspec.filename).exists(), (
        f"Migration file {mspec.chain}/{mspec.filename} not found"
    )


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_metadata(mspec: MigrationSpec) -> None:
    module = _load_migration_module(mspec.chain, mspec.filename)

    assert getattr(module, "revision", None) == mspec.revision
    assert getattr(module, "down_revision", None) == mspec.down_revision
    assert getattr(module, "branch_labels", None) == mspec.branch_labels
    assert getattr(module, "depends_on", None) == mspec.depends_on


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_upgrade_callable(mspec: MigrationSpec) -> None:
    module = _load_migration_module(mspec.chain, mspec.filename)
    assert hasattr(module, "upgrade") and callable(module.upgrade)


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_downgrade_callable(mspec: MigrationSpec) -> None:
    module = _load_migration_module(mspec.chain, mspec.filename)
    assert hasattr(module, "downgrade") and callable(module.downgrade)
