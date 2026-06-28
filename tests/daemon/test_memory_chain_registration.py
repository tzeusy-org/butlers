"""Tests for migration chain registration in the daemon migration runner.

Covers memory module chain and relationship butler migration chain integrity.
"""

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

ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_migrations_module():
    return _load_module(_MIGRATIONS_PATH, "butlers.migrations")


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
RELATIONSHIP_MIGRATIONS_DIR = ROSTER_DIR / "relationship" / "migrations"


class TestMemoryChainRegistration:
    """Verify that the memory chain is registered and discoverable."""

    def test_memory_chain_registration_ordering_and_metadata(self) -> None:
        """memory not in shared; in get_all_chains; shared come first; chain dir + files correct; linear."""
        assert "memory" not in _SHARED_CHAINS
        assert "memory" in get_all_chains()
        assert "core" in _SHARED_CHAINS

        chains = get_all_chains()
        shared_indices = [i for i, c in enumerate(chains) if c in _SHARED_CHAINS]
        non_shared_indices = [i for i, c in enumerate(chains) if c not in _SHARED_CHAINS]
        if shared_indices and non_shared_indices:
            assert max(shared_indices) < min(non_shared_indices)

        chain_dir = _resolve_chain_dir("memory")
        assert chain_dir is not None and chain_dir.is_dir()
        migration_files = sorted(
            f.name for f in chain_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"
        )
        assert migration_files == [
            "001_memory_schema.py",
            "002_seed_predicates.py",
            "003_wellness_predicates.py",
            "004_embedding_model_version.py",
            "005_drop_embedding_versions.py",
            "006_drop_rule_applications.py",
        ]
        assert has_butler_chain("memory") is False
        assert has_butler_chain("nonexistent_butler_xyz") is False

        _EXPECTED_CHAIN = [
            ("001_memory_schema.py", "mem_001", None),
            ("002_seed_predicates.py", "mem_002", "mem_001"),
            ("003_wellness_predicates.py", "mem_003", "mem_002"),
            ("004_embedding_model_version.py", "mem_004", "mem_003"),
            ("005_drop_embedding_versions.py", "mem_005", "mem_004"),
            ("006_drop_rule_applications.py", "mem_006", "mem_005"),
        ]

        def _load_migration(filename: str):
            filepath = MEMORY_MIGRATIONS_DIR / filename
            spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), filepath)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        revisions = []
        chain_map = {}
        for filename, expected_rev, expected_down_rev in _EXPECTED_CHAIN:
            assert (MEMORY_MIGRATIONS_DIR / filename).exists(), f"Missing migration: {filename}"
            mod = _load_migration(filename)
            assert mod.revision == expected_rev
            assert mod.down_revision == expected_down_rev
            assert mod.depends_on is None
            assert callable(getattr(mod, "upgrade", None))
            revisions.append(mod.revision)
            chain_map[mod.revision] = mod.down_revision

        root = _load_migration(_EXPECTED_CHAIN[0][0])
        assert root.branch_labels == ("memory",)
        assert len(revisions) == len(set(revisions))
        current = "mem_006"
        path = [current]
        while chain_map.get(current) is not None:
            current = chain_map[current]
            path.append(current)
        path.reverse()
        assert path == ["mem_001", "mem_002", "mem_003", "mem_004", "mem_005", "mem_006"]


class TestRelationshipChainRegistration:
    """Migration chain integrity for the relationship butler."""

    _EXPECTED_CHAIN = [
        ("001_relationship_tables.py", "rel_001", None),
        ("002_align_contacts_schema.py", "rel_002", "rel_001"),
    ]

    @classmethod
    def _load_migration(cls, filename: str):
        return _load_module(RELATIONSHIP_MIGRATIONS_DIR / filename, filename.removesuffix(".py"))

    def test_relationship_chain_structure_and_metadata(self) -> None:
        """Directory resolves correctly; 002 file is canonical; revisions/labels correct; linear."""
        # Directory resolves
        chain_dir = _mod._resolve_chain_dir("relationship")
        assert chain_dir == RELATIONSHIP_MIGRATIONS_DIR

        # 002 file canonical
        files_002 = sorted(p.name for p in RELATIONSHIP_MIGRATIONS_DIR.glob("002_*.py"))
        assert files_002 == ["002_align_contacts_schema.py"]

        # Revision/down_revision/branch_labels
        revisions = []
        chain_map = {}
        for filename, expected_revision, expected_down_revision in self._EXPECTED_CHAIN:
            migration = self._load_migration(filename)
            assert migration.revision == expected_revision
            assert migration.down_revision == expected_down_revision
            if expected_revision == "rel_001":
                assert migration.branch_labels == ("relationship",)
            else:
                assert migration.branch_labels is None
            revisions.append(migration.revision)
            chain_map[migration.revision] = migration.down_revision

        # Linear, no duplicates
        assert len(revisions) == len(set(revisions))
        current = "rel_002"
        path = [current]
        while chain_map.get(current) is not None:
            current = chain_map[current]
            path.append(current)
        path.reverse()
        assert path == ["rel_001", "rel_002"]
