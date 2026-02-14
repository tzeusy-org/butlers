"""Programmatic Alembic migration runner for butlers.

Allows the daemon to run migrations at startup without shelling out to the
Alembic CLI. Supports targeting a specific version chain (core or butler-specific).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic.config import Config

from alembic import command

logger = logging.getLogger(__name__)

# Root of the alembic directory (sibling to src/)
ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"

# Root of the butler config directories (sibling to src/)
ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"

# Root of the modules directory (src/butlers/modules/)
MODULES_DIR = Path(__file__).resolve().parent / "modules"

# Shared chains: always included regardless of butler identity
_SHARED_CHAINS = ["core"]


def _discover_module_chains() -> list[str]:
    """Discover module-local migration chains from module directories.

    Scans ``src/butlers/modules/*/migrations/`` for directories that contain
    at least one ``.py`` migration file (excluding ``__init__.py``).

    Returns:
        Sorted list of module names that have a migrations/ folder with files.
    """
    if not MODULES_DIR.is_dir():
        return []
    chains = []
    for entry in sorted(MODULES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        mig_dir = entry / "migrations"
        if not mig_dir.is_dir():
            continue
        migration_files = [
            f for f in mig_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"
        ]
        if migration_files:
            chains.append(entry.name)
    return chains


def _discover_butler_chains() -> list[str]:
    """Discover butler-specific migration chains from butler config dirs.

    Scans ``roster/*/migrations/`` for directories that contain at least one
    ``.py`` migration file (excluding ``__init__.py``).

    Returns:
        Sorted list of butler names that have a migrations/ folder with files.
    """
    if not ROSTER_DIR.is_dir():
        return []
    chains = []
    for entry in sorted(ROSTER_DIR.iterdir()):
        if not entry.is_dir():
            continue
        mig_dir = entry / "migrations"
        if not mig_dir.is_dir():
            continue
        migration_files = [
            f for f in mig_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"
        ]
        if migration_files:
            chains.append(entry.name)
    return chains


def _resolve_chain_dir(chain: str) -> Path | None:
    """Resolve the filesystem path for a given chain name.

    Shared chains (core) live in ``alembic/versions/<chain>/``.
    Module chains live in ``src/butlers/modules/<chain>/migrations/``.
    Butler-specific chains live in ``roster/<chain>/migrations/``.

    Returns:
        The chain directory Path if it exists, otherwise None.
    """
    if chain in _SHARED_CHAINS:
        chain_dir = ALEMBIC_DIR / "versions" / chain
        if chain_dir.is_dir():
            return chain_dir

    # Check module-local migrations
    module_chain_dir = MODULES_DIR / chain / "migrations"
    if module_chain_dir.is_dir():
        return module_chain_dir

    # Check butler-specific migrations
    butler_chain_dir = ROSTER_DIR / chain / "migrations"
    if butler_chain_dir.is_dir():
        return butler_chain_dir

    return None


def get_all_chains() -> list[str]:
    """Return all recognized version chains (shared + module + butler-specific).

    Shared chains are listed first, followed by module chains, then dynamically
    discovered butler-specific chains.  Shared chain names are excluded from the
    module/butler discovery results to avoid duplicates.
    """
    shared = [c for c in _SHARED_CHAINS if _resolve_chain_dir(c) is not None]
    shared_set = set(shared)
    modules = [c for c in _discover_module_chains() if c not in shared_set]
    module_set = set(modules)
    butlers = [c for c in _discover_butler_chains() if c not in shared_set and c not in module_set]
    return shared + modules + butlers


def _build_alembic_config(db_url: str, chains: list[str] | None = None) -> Config:
    """Build an Alembic Config pointing at the correct version directories.

    Args:
        db_url: SQLAlchemy-compatible database URL.
        chains: List of version chain names to include. Defaults to all chains.

    Returns:
        A configured alembic.config.Config instance.
    """
    ini_path = ALEMBIC_DIR / "alembic.ini"
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", db_url)

    # Always include ALL version locations so Alembic can resolve every
    # revision in alembic_version, even when upgrading a single branch.
    all_chains = get_all_chains()
    locations = []
    for chain in all_chains:
        chain_dir = _resolve_chain_dir(chain)
        if chain_dir is not None:
            locations.append(str(chain_dir))
    config.set_main_option("version_locations", os.pathsep.join(locations))

    return config


def has_butler_chain(butler_name: str) -> bool:
    """Check for a butler-specific migration chain not owned by a module.

    Butler-specific chains are discovered in ``roster/<butler_name>/migrations/``.
    If a module migration chain exists at ``src/butlers/modules/<butler_name>/migrations/``
    and contains revisions, this function returns ``False`` so module chains
    take precedence.

    Args:
        butler_name: The butler identity name (e.g. ``"relationship"``).

    Returns:
        ``True`` if a non-empty, non-module migration chain exists for the butler.
    """
    module_chain_dir = MODULES_DIR / butler_name / "migrations"
    if module_chain_dir.is_dir():
        module_migration_files = [
            f for f in module_chain_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"
        ]
        if module_migration_files:
            return False

    chain_dir = ROSTER_DIR / butler_name / "migrations"
    if not chain_dir.is_dir():
        return False
    migration_files = [
        f for f in chain_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"
    ]
    return len(migration_files) > 0


async def run_migrations(db_url: str, chain: str = "core") -> None:
    """Run Alembic migrations programmatically for a specific chain.

    This is the primary entry point for running migrations from the butler
    daemon. It configures Alembic to use the correct version directory and
    upgrades to the latest revision.

    Args:
        db_url: SQLAlchemy-compatible database URL
            (e.g. ``postgresql://user:pass@host:port/dbname``).
        chain: Version chain to migrate. Must be one of the recognized chains
            (core, mailbox, approvals, or any butler name with a migrations/
            directory). Pass ``"all"`` to migrate all chains.
    """
    if chain == "all":
        chains = get_all_chains()
    else:
        chains = [chain]

    config = _build_alembic_config(db_url, chains)

    if chain == "all":
        # Upgrade all chains to head
        logger.info("Running all migration chains to head")
        command.upgrade(config, "heads")
    else:
        # Upgrade a specific chain branch to its head
        logger.info("Running %s migration chain to head", chain)
        command.upgrade(config, f"{chain}@head")
