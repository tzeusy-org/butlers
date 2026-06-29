"""Canonical migration chain-integrity tests covering all migration chains.

Verifies for every chain:
1. All migration files exist on disk.
2. Each migration has callable upgrade() and downgrade().
3. Chain is internally consistent (each revision's down_revision points to an
   existing revision in the same chain, or None for the root). Tuple/list
   down_revisions (merge migrations) are supported.
4. Chain has exactly one root migration.

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ALL_CHAINS = [
    "core",
    "switchboard",
    "finance",
    "education",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "travel",
    "relationship",
    "approvals",
    "contacts",
    "google_drive",
    "mailbox",
    "memory",
    "whatsapp",
    "chronicler",
]


def _resolve_chain_dir(chain: str) -> Path | None:
    from butlers.migrations import _resolve_chain_dir as _rcd

    return _rcd(chain)


def _load_module(chain_dir: Path, filename: str):
    path = chain_dir / filename
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_chain_modules(chain: str) -> list[object]:
    chain_dir = _resolve_chain_dir(chain)
    assert chain_dir is not None, f"Chain {chain!r} not found"
    files = sorted(f.name for f in chain_dir.glob("*.py") if not f.name.startswith("_"))
    return [_load_module(chain_dir, f) for f in files]


def test_all_migration_chains_integrity() -> None:
    """All 18 chains: files exist, up/downgrade callable, chain graph consistent, single root."""
    for chain in ALL_CHAINS:
        modules = _load_chain_modules(chain)
        assert len(modules) >= 1, f"Chain {chain!r} has no migration files"

        revisions: dict[str, object] = {}
        for m in modules:
            rev = getattr(m, "revision", None)
            assert rev is not None, f"{chain}: migration missing 'revision'"
            assert rev not in revisions, f"{chain}: duplicate migration revision {rev!r}"
            assert callable(getattr(m, "upgrade", None)), f"{chain}/{rev}: upgrade() not callable"
            assert callable(getattr(m, "downgrade", None)), (
                f"{chain}/{rev}: downgrade() not callable"
            )
            revisions[rev] = m

        for m in modules:
            rev = getattr(m, "revision")
            down_rev = getattr(m, "down_revision", None)
            if down_rev is not None:
                # down_revision may be a string (linear chain) or tuple/list (merge migration)
                parents = (down_rev,) if isinstance(down_rev, str) else tuple(down_rev)
                for parent in parents:
                    assert parent in revisions, (
                        f"{chain}/{rev}: down_revision parent {parent!r} not in chain"
                    )

        roots = [m for m in modules if getattr(m, "down_revision", None) is None]
        assert len(roots) == 1, f"Chain {chain!r}: expected 1 root, found {len(roots)}"
