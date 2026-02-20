"""Tests for memory migration chain registration in the daemon migration runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the migrations module from src/butlers/migrations.py
# ---------------------------------------------------------------------------
_MIGRATIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "migrations.py"
)


def _load_migrations_module():
    spec = importlib.util.spec_from_file_location("butlers.migrations", _MIGRATIONS_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_migrations_module()
get_all_chains = _mod.get_all_chains
_SHARED_CHAINS = _mod._SHARED_CHAINS
_resolve_chain_dir = _mod._resolve_chain_dir
has_butler_chain = _mod.has_butler_chain

# ---------------------------------------------------------------------------
# Migration chain discovery for module-local memory migrations
# ---------------------------------------------------------------------------
MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "modules"
MEMORY_MIGRATIONS_DIR = MODULES_DIR / "memory" / "migrations"


class TestMemoryChainRegistration:
    """Verify that the memory chain is registered and discoverable."""

    def test_memory_not_in_shared_chains(self) -> None:
        """'memory' is module-owned and should not be listed in _SHARED_CHAINS."""
        assert "memory" not in _SHARED_CHAINS

    def test_memory_in_get_all_chains(self) -> None:
        """'memory' should appear in the list returned by get_all_chains()."""
        chains = get_all_chains()
        assert "memory" in chains

    def test_memory_chain_dir_resolves(self) -> None:
        """_resolve_chain_dir('memory') should return a valid directory."""
        chain_dir = _resolve_chain_dir("memory")
        assert chain_dir is not None
        assert chain_dir.is_dir()

    def test_memory_chain_dir_contains_migrations(self) -> None:
        """The resolved memory chain directory should contain migration files."""
        chain_dir = _resolve_chain_dir("memory")
        assert chain_dir is not None
        migration_files = sorted(
            f.name for f in chain_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"
        )
        assert migration_files == ["001_memory_baseline.py"], (
            f"Expected one baseline file, found {migration_files}"
        )

    def test_core_also_in_shared_chains(self) -> None:
        """'core' should also be in shared chains (sanity check)."""
        assert "core" in _SHARED_CHAINS

    def test_shared_chains_come_first_in_all_chains(self) -> None:
        """Shared chains should appear before butler-specific chains."""
        chains = get_all_chains()
        # Find the last shared chain index and the first non-shared index
        shared_indices = [i for i, c in enumerate(chains) if c in _SHARED_CHAINS]
        non_shared_indices = [i for i, c in enumerate(chains) if c not in _SHARED_CHAINS]
        if shared_indices and non_shared_indices:
            assert max(shared_indices) < min(non_shared_indices), (
                "Shared chains should appear before non-shared chains"
            )

    def test_has_butler_chain_for_memory(self) -> None:
        """has_butler_chain('memory') should be False when module chain owns it."""
        assert has_butler_chain("memory") is False

    def test_has_butler_chain_for_nonexistent(self) -> None:
        """has_butler_chain for a non-existent butler returns False."""
        assert has_butler_chain("nonexistent_butler_xyz") is False


class TestBaselineRevisionChain:
    """Validate the single baseline revision chain for memory."""

    EXPECTED_CHAIN = [
        ("001_memory_baseline.py", "mem_001", None),
    ]

    @staticmethod
    def _load_migration(filename: str):
        """Load a migration module by filename from the memory migrations dir."""
        filepath = MEMORY_MIGRATIONS_DIR / filename
        spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), filepath)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_all_expected_migration_files_exist(self) -> None:
        """The baseline migration file should exist on disk."""
        for filename, _, _ in self.EXPECTED_CHAIN:
            filepath = MEMORY_MIGRATIONS_DIR / filename
            assert filepath.exists(), f"Missing migration: {filepath}"

    def test_revision_chain_links(self) -> None:
        """Each migration's down_revision should point to the previous revision."""
        for filename, expected_rev, expected_down_rev in self.EXPECTED_CHAIN:
            mod = self._load_migration(filename)
            assert mod.revision == expected_rev, (
                f"{filename}: expected revision={expected_rev}, got {mod.revision}"
            )
            assert mod.down_revision == expected_down_rev, (
                f"{filename}: expected down_revision={expected_down_rev}, got {mod.down_revision}"
            )

    def test_branch_label_present_on_baseline_root(self) -> None:
        """The baseline root should have branch_labels=('memory',)."""
        filename, _, _ = self.EXPECTED_CHAIN[0]
        mod = self._load_migration(filename)
        assert mod.branch_labels == ("memory",), f"{filename} should have branch_labels=('memory',)"

    def test_no_migration_has_depends_on(self) -> None:
        """All migrations should have depends_on=None (chaining via down_revision)."""
        for filename, _, _ in self.EXPECTED_CHAIN:
            mod = self._load_migration(filename)
            assert mod.depends_on is None, f"{filename} should have depends_on=None"

    def test_all_migrations_have_upgrade_and_downgrade(self) -> None:
        """Every migration must define both upgrade() and downgrade() callables."""
        for filename, _, _ in self.EXPECTED_CHAIN:
            mod = self._load_migration(filename)
            assert callable(getattr(mod, "upgrade", None)), f"{filename} missing upgrade()"
            assert callable(getattr(mod, "downgrade", None)), f"{filename} missing downgrade()"

    def test_no_duplicate_revisions(self) -> None:
        """Each revision ID in the chain must be unique."""
        revisions = []
        for filename, _, _ in self.EXPECTED_CHAIN:
            mod = self._load_migration(filename)
            revisions.append(mod.revision)
        assert len(revisions) == len(set(revisions)), f"Duplicate revisions found: {revisions}"

    def test_chain_is_linear(self) -> None:
        """The chain should form a single linear sequence with one head."""
        chain_map = {}
        for filename, _, _ in self.EXPECTED_CHAIN:
            mod = self._load_migration(filename)
            chain_map[mod.revision] = mod.down_revision

        current = "mem_001"
        path = [current]
        while chain_map.get(current) is not None:
            current = chain_map[current]
            path.append(current)

        path.reverse()
        assert path == ["mem_001"], f"Expected linear chain [mem_001], got {path}"
