"""Tests for relationship migration chain integrity."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "src" / "butlers" / "migrations.py"
)
ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"
RELATIONSHIP_MIGRATIONS_DIR = ROSTER_DIR / "relationship" / "migrations"


EXPECTED_CHAIN = [
    ("001_relationship_tables.py", "rel_001", None),
]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_migrations_module():
    return _load_module(_MIGRATIONS_PATH, "butlers.migrations")


def _load_relationship_migration(filename: str):
    path = RELATIONSHIP_MIGRATIONS_DIR / filename
    return _load_module(path, filename.removesuffix(".py"))


def test_relationship_chain_directory_resolves() -> None:
    mod = _load_migrations_module()
    chain_dir = mod._resolve_chain_dir("relationship")
    assert chain_dir == RELATIONSHIP_MIGRATIONS_DIR


def test_relationship_chain_has_no_legacy_duplicate_002_prefix_files() -> None:
    legacy_files = sorted(p.name for p in RELATIONSHIP_MIGRATIONS_DIR.glob("002_*.py"))
    assert legacy_files == []


def test_relationship_chain_expected_files_and_links() -> None:
    for filename, expected_revision, expected_down_revision in EXPECTED_CHAIN:
        migration = _load_relationship_migration(filename)
        assert migration.revision == expected_revision
        assert migration.down_revision == expected_down_revision


def test_relationship_chain_branch_labels() -> None:
    for filename, expected_revision, _ in EXPECTED_CHAIN:
        migration = _load_relationship_migration(filename)
        if expected_revision == "rel_001":
            assert migration.branch_labels == ("relationship",)
        else:
            assert migration.branch_labels is None


def test_relationship_chain_has_unique_revisions_and_is_linear() -> None:
    chain_map = {}
    revisions = []
    for filename, _, _ in EXPECTED_CHAIN:
        migration = _load_relationship_migration(filename)
        chain_map[migration.revision] = migration.down_revision
        revisions.append(migration.revision)

    assert len(revisions) == len(set(revisions))

    current = "rel_001"
    path = [current]
    while chain_map.get(current) is not None:
        current = chain_map[current]
        path.append(current)

    path.reverse()
    assert path == ["rel_001"]
