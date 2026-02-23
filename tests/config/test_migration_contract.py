"""Canonical migration contract tests.

This module is the single source of truth for generic migration file contracts:
  - File existence
  - Required Alembic metadata attributes (revision, down_revision, branch_labels, depends_on)
  - Callable guards (upgrade, downgrade)
  - Chain membership

Role-specific schema checks (column types, constraints, index names, DDL content)
live in the per-butler unit test files alongside the migration they describe.

Previously these five boilerplate checks were duplicated verbatim in:
  - tests/config/test_switchboard_connector_heartbeat_migration_unit.py
  - tests/config/test_switchboard_notifications_migration_unit.py

Those files now only contain role-specific assertions.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class MigrationSpec:
    """Describes one migration file whose generic contract should be verified."""

    chain: str
    filename: str
    revision: str
    down_revision: str | None
    branch_labels: tuple[str, ...] | None
    depends_on: str | tuple[str, ...] | None = None

    @property
    def id(self) -> str:
        return f"{self.chain}/{self.filename}"


# ---------------------------------------------------------------------------
# Registry of migrations under generic contract verification.
#
# Add an entry here for any migration whose boilerplate (file-exists, metadata,
# upgrade/downgrade callable) should be checked without a standalone unit file.
# Role-specific DDL/schema checks still belong in per-butler unit test files.
# ---------------------------------------------------------------------------

_MIGRATION_SPECS = [
    # Switchboard chain – non-root migrations whose boilerplate was previously
    # duplicated across two standalone _unit.py files.
    MigrationSpec(
        chain="switchboard",
        filename="003_add_notifications_table.py",
        revision="sw_003",
        down_revision="sw_002",
        branch_labels=None,
        depends_on=None,
    ),
    MigrationSpec(
        chain="switchboard",
        filename="013_create_connector_heartbeat_tables.py",
        revision="sw_013",
        down_revision="sw_012",
        branch_labels=None,
        depends_on=None,
    ),
    MigrationSpec(
        chain="switchboard",
        filename="008_partition_message_inbox_lifecycle.py",
        revision="sw_008",
        down_revision="sw_007",
        branch_labels=None,
        depends_on=None,
    ),
    MigrationSpec(
        chain="switchboard",
        filename="018_create_backfill_jobs.py",
        revision="sw_018",
        down_revision="sw_017",
        branch_labels=None,
        depends_on=None,
    ),
    # Finance butler chain root migration
    MigrationSpec(
        chain="finance",
        filename="001_finance_tables.py",
        revision="finance_001",
        down_revision=None,
        branch_labels=("finance",),
        depends_on=None,
    ),
]


def _load_migration_module(chain: str, filename: str):
    """Load and return a migration module by chain and filename."""
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
    """Migration file must exist at the expected path in its chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir(mspec.chain)
    assert chain_dir is not None, f"Chain {mspec.chain!r} should exist"
    assert (chain_dir / mspec.filename).exists(), (
        f"Migration file {mspec.chain}/{mspec.filename} not found"
    )


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_metadata(mspec: MigrationSpec) -> None:
    """Alembic metadata attributes must match the declared spec."""
    module = _load_migration_module(mspec.chain, mspec.filename)

    assert getattr(module, "revision", None) == mspec.revision, (
        f"revision mismatch: expected {mspec.revision!r}"
    )
    assert getattr(module, "down_revision", None) == mspec.down_revision, (
        f"down_revision mismatch: expected {mspec.down_revision!r}"
    )
    assert getattr(module, "branch_labels", None) == mspec.branch_labels, (
        f"branch_labels mismatch: expected {mspec.branch_labels!r}"
    )
    assert getattr(module, "depends_on", None) == mspec.depends_on, (
        f"depends_on mismatch: expected {mspec.depends_on!r}"
    )


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_upgrade_callable(mspec: MigrationSpec) -> None:
    """upgrade() must be defined and callable."""
    module = _load_migration_module(mspec.chain, mspec.filename)
    assert hasattr(module, "upgrade") and callable(module.upgrade), (
        f"{mspec.chain}/{mspec.filename}: upgrade() must be callable"
    )


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_downgrade_callable(mspec: MigrationSpec) -> None:
    """downgrade() must be defined and callable."""
    module = _load_migration_module(mspec.chain, mspec.filename)
    assert hasattr(module, "downgrade") and callable(module.downgrade), (
        f"{mspec.chain}/{mspec.filename}: downgrade() must be callable"
    )


@pytest.mark.parametrize("mspec", _MIGRATION_SPECS, ids=lambda m: m.id)
def test_migration_chain_membership(mspec: MigrationSpec) -> None:
    """Migration file must appear in its chain directory."""
    from butlers.migrations import _resolve_chain_dir

    chain_dir = _resolve_chain_dir(mspec.chain)
    assert chain_dir is not None, f"Chain {mspec.chain!r} should exist"

    present = [f.name for f in chain_dir.glob("*.py") if f.name != "__init__.py"]
    assert mspec.filename in present, f"{mspec.filename} should be in {mspec.chain} chain directory"
