"""Programmatic Alembic migration runner for butlers.

Runs operator-selected migrations without shelling out to the Alembic CLI.
Serving startup only verifies the delivered schema. Supports a selected version
chain (core or butler-specific) on the held maintenance connection.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from alembic import command
from butlers.bootstrap_prerequisite import check_bootstrap_connection

if TYPE_CHECKING:
    from butlers.modules.base import Module

logger = logging.getLogger(__name__)

_MIGRATION_LOCK = (1112888402, 6)


def maintenance_profile(connection) -> str:
    """Bind privileged maintenance to the actual normal database owner.

    Both identities are catalog observations, not a caller-supplied role name.
    Requiring this before any chain runs prevents a partial administrative
    migration from leaving its ordinary objects owned by the maintenance login.
    """
    from butlers.db_authority.roles import AuthorityError, bootstrap_role_manifest

    database, owner, valid = connection.exec_driver_sql("""
        SELECT d.datname, owner.rolname,
          actor.rolsuper AND actor.rolname=session_user
          AND NOT owner.rolsuper AND NOT owner.rolcreaterole AND NOT owner.rolcreatedb
          AND NOT owner.rolreplication AND NOT owner.rolbypassrls AND NOT owner.rolinherit
          AND actor.oid<>owner.oid
        FROM pg_catalog.pg_database d
        JOIN pg_catalog.pg_roles owner ON owner.oid=d.datdba
        JOIN pg_catalog.pg_roles actor ON actor.rolname=session_user
        WHERE d.datname=current_database()
    """).one()
    source = connection.info.get("butlers.source_database", database)
    bootstrap_role_manifest(database, owner, source_database=source)
    if valid is not True:
        raise AuthorityError("Explicit migration maintenance/ownership prerequisite is unavailable")
    return owner


@contextmanager
def ordinary_migration_identity(connection):
    """Use the normal owner for ordinary DDL on the same maintenance session."""
    from butlers.db_authority.apply import quote_identifier

    owner = maintenance_profile(connection)
    connection.exec_driver_sql(f"SET ROLE {quote_identifier(owner)}")
    primary = None
    try:
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            connection.exec_driver_sql("RESET ROLE")
        except BaseException:
            if primary is None:
                raise
            primary.add_note("Migration role restoration was incomplete")


@contextmanager
def migration_session(db_url: str, *, source_database: str | None = None):
    """Own one physical session/lock through all chains and final verification."""
    engine = create_engine(
        db_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=300000 -c lock_timeout=5000",
        },
    )
    connection = None
    locked = False
    primary = None
    try:
        connection = engine.connect()
        if source_database is not None:
            from butlers.db_authority.roles import compile_deployment_profiles

            selected = connection.exec_driver_sql("SELECT current_database()").scalar_one()
            compile_deployment_profiles(selected, source_database=source_database)
            connection.info["butlers.source_database"] = source_database
        check_bootstrap_connection(connection)
        maintenance_profile(connection)
        connection.commit()
        deadline = time.monotonic() + 60
        while True:
            locked = (
                connection.execute(
                    text("SELECT pg_catalog.pg_try_advisory_lock(:family, :operation)"),
                    {"family": _MIGRATION_LOCK[0], "operation": _MIGRATION_LOCK[1]},
                ).scalar_one()
                is True
            )
            connection.commit()
            if locked:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Selected database migration lock is unavailable")
            time.sleep(0.1)
        yield connection
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_error = None
        try:
            if connection is not None:
                connection.rollback()
                if locked:
                    released = connection.execute(
                        text("SELECT pg_catalog.pg_advisory_unlock(:family, :operation)"),
                        {"family": _MIGRATION_LOCK[0], "operation": _MIGRATION_LOCK[1]},
                    ).scalar_one()
                    if released is not True:
                        raise RuntimeError("Selected database migration lock release failed")
                    connection.commit()
        except BaseException as exc:
            cleanup_error = exc
        for cleanup, description in (
            (None if connection is None else connection.close, "connection close"),
            (engine.dispose, "engine disposal"),
        ):
            if cleanup is None:
                continue
            try:
                cleanup()
            except BaseException as exc:
                retained = primary if primary is not None else cleanup_error
                if retained is None:
                    cleanup_error = exc
                else:
                    retained.add_note(f"Selected migration {description} was incomplete")
        if cleanup_error is not None:
            if primary is None:
                raise cleanup_error
            primary.add_note("Selected migration session cleanup was incomplete")


def finalize_authority(
    connection, *, source_database: str | None = None, plans: tuple | None = None
) -> None:
    """Converge and verify final authority before releasing the migration lock."""
    from butlers.db_authority.apply import converge_authority

    converge_authority(connection, source_database=source_database, plans=plans)


# Root of the alembic directory (sibling to src/)
ALEMBIC_DIR = Path(__file__).resolve().parent.parent.parent / "alembic"

# Root of the butler config directories (sibling to src/)
ROSTER_DIR = Path(__file__).resolve().parent.parent.parent / "roster"

# Root of the modules directory (src/butlers/modules/)
MODULES_DIR = Path(__file__).resolve().parent / "modules"

# Shared chains: always included regardless of butler identity
_SHARED_CHAINS = ["core"]
_TARGET_SCHEMA_OPTION = "butlers.target_schema"
_VERSION_TABLE_SCHEMA_OPTION = "version_table_schema"
_VALID_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


@dataclass(frozen=True)
class _ChainRootFamily:
    """One discoverable shape of migration-chain root directory.

    Pairs the filesystem resolution rule with the chains it applies to; the
    tuple order below is ``_resolve_chain_dir``'s resolution order.
    """

    dir_for_chain: Callable[[str], Path]
    applies_to_chain: Callable[[str], bool] = lambda chain: True  # noqa: E731


_CHAIN_ROOT_FAMILIES: tuple[_ChainRootFamily, ...] = (
    _ChainRootFamily(
        applies_to_chain=lambda chain: chain in _SHARED_CHAINS,
        dir_for_chain=lambda chain: ALEMBIC_DIR / "versions" / chain,
    ),
    _ChainRootFamily(
        dir_for_chain=lambda chain: MODULES_DIR / chain / "migrations",
    ),
    _ChainRootFamily(
        dir_for_chain=lambda chain: ROSTER_DIR / chain / "migrations",
    ),
)


def _resolve_chain_dir(chain: str) -> Path | None:
    """Resolve the filesystem path for a given chain name.

    Tries each recognized root family in ``_CHAIN_ROOT_FAMILIES`` (shared
    chains under ``alembic/versions/<chain>/``, module chains under
    ``src/butlers/modules/<chain>/migrations/``, then butler-specific chains
    under ``roster/<chain>/migrations/``) and returns the first directory
    that exists.

    Returns:
        The chain directory Path if it exists, otherwise None.
    """
    for family in _CHAIN_ROOT_FAMILIES:
        if not family.applies_to_chain(chain):
            continue
        chain_dir = family.dir_for_chain(chain)
        if chain_dir.is_dir():
            return chain_dir
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


def get_module_migration_chains(modules: Iterable[Module], butler_name: str) -> dict[str, str]:
    """Map admitted module names to their existing, non-empty migration directories.

    Roster modules can have a different public name from their owning directory
    (``home_assistant`` lives in ``roster/home/modules``). Resolve that layout
    without a constructor or a module-provided migration hook. The butler's own
    chain already ran in the preceding phase and must not run twice.
    """
    available = set(get_all_chains()) - set(_SHARED_CHAINS)
    chains: dict[str, str] = {}
    for module in modules:
        chain = module.name
        if chain not in available:
            source = Path(inspect.getfile(type(module))).resolve()
            try:
                relative = source.relative_to(ROSTER_DIR.resolve())
            except ValueError:
                continue
            # Only the roster's root module owns its roster chain. A broker
            # submodule must not acquire the entire roster chain by proximity.
            if len(relative.parts) != 3 or relative.parts[1:] != ("modules", "__init__.py"):
                continue
            chain = relative.parts[0]
        if chain == butler_name and has_butler_chain(butler_name):
            continue
        if chain in available and _resolve_chain_dir(chain) is not None:
            chains[module.name] = chain
    return chains


def _normalize_schema(schema: str | None) -> str | None:
    """Normalize and validate a schema name for migration execution."""
    if schema is None:
        return None
    normalized = schema.strip()
    if not normalized:
        return None
    if _VALID_SCHEMA_RE.fullmatch(normalized) is None:
        raise ValueError(f"Invalid migration schema name: {schema!r}")
    return normalized


def _build_alembic_config(
    db_url: str, chains: list[str] | None = None, target_schema: str | None = None
) -> Config:
    """Build an Alembic Config pointing at the correct version directories.

    Args:
        db_url: SQLAlchemy-compatible database URL.
        chains: List of version chain names to include. Defaults to all chains.
        target_schema: Optional target schema for schema-scoped migration runs.

    Returns:
        A configured alembic.config.Config instance.
    """
    ini_path = ALEMBIC_DIR / "alembic.ini"
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    # Alembic Config uses configparser interpolation; percent-encoded DB URLs
    # (for example libpq options with %3D/%2C) must escape '%' as '%%'.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    normalized_schema = _normalize_schema(target_schema)
    if normalized_schema is not None:
        config.set_main_option(_TARGET_SCHEMA_OPTION, normalized_schema)
        config.set_main_option(_VERSION_TABLE_SCHEMA_OPTION, normalized_schema)

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


def _resolve_target_chains(chain: str) -> list[str]:
    """Resolve the deterministic chain execution order."""
    if chain == "all":
        return get_all_chains()
    return [chain]


def _chain_script_directory(chain: str) -> ScriptDirectory:
    """Build a ScriptDirectory scoped to exactly one migration chain.

    Unlike ``_build_alembic_config`` (which always includes every chain's
    ``version_locations`` so a shared per-schema ``alembic_version`` table can
    resolve any revision during an upgrade), this scopes ``version_locations``
    to *chain* alone so ``get_heads()``/``walk_revisions()`` only ever see
    that one chain's self-contained revision graph. No database connection is
    made — this is a pure filesystem scan of the chain's version directory.
    """
    chain_dir = _resolve_chain_dir(chain)
    if chain_dir is None:
        raise ValueError(f"Unknown migration chain: {chain!r}")
    config = Config(str(ALEMBIC_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("version_locations", str(chain_dir))
    return ScriptDirectory.from_config(config)


def get_chain_head(chain: str) -> str:
    """Return the current codebase head revision for one migration chain.

    Used by the migration-drift sentinel (bu-9r3hd.1) to compare "what the
    codebase says should be applied" against what a schema's
    ``alembic_version`` table actually holds. A chain is expected to have
    exactly one head; more than one (an unmerged fork left in the codebase)
    or zero (an empty chain) raises rather than silently picking one, since
    either is itself a codebase defect worth surfacing loudly.
    """
    heads = _chain_script_directory(chain).get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            f"Migration chain {chain!r} has {len(heads)} head(s) (expected exactly 1): "
            f"{sorted(heads)}"
        )
    return heads[0]


async def require_installed_chain(pool, *, chain: str, schema: str | None) -> None:
    """Read-only serving admission; only the selected migrator may install heads."""
    target = _normalize_schema(schema) or "public"
    expected = get_chain_head(chain)
    async with pool.acquire() as connection:
        present = await connection.fetchval(
            f'SELECT EXISTS (SELECT FROM "{target}".alembic_version WHERE version_num = $1)',
            expected,
        )
    if present is not True:
        raise RuntimeError("Required database revision is unavailable")


def get_chain_revision_ids(chain: str) -> frozenset[str]:
    """Return every revision id that belongs to one migration chain.

    Revision ids are chain-specific by naming convention (e.g. ``core_NNN``,
    ``home_NNN``), so intersecting a schema's applied ``alembic_version`` rows
    with this set unambiguously identifies that chain's applied state even
    though independent chains share one ``alembic_version`` table per schema.
    """
    return frozenset(rev.revision for rev in _chain_script_directory(chain).walk_revisions())


def get_chain_revision_path(chain: str, revision: str | None = None) -> frozenset[str]:
    """Resolve a selected target's actual ancestors without lexical ID guesses.

    The bounded administrative producer and authority compiler share this
    checked-in graph. An unknown target cannot stand in for a current catalog
    or make a later namespace/interface appear installed.
    """
    target = get_chain_head(chain) if revision is None else revision
    if not isinstance(target, str) or target not in get_chain_revision_ids(chain):
        raise RuntimeError("Selected migration revision is not source-owned")
    script = _chain_script_directory(chain)
    return frozenset(row.revision for row in script.iterate_revisions(target, "base"))


def _upgrade_chain(config: Config, chain: str, schema: str | None) -> None:
    """Upgrade a single chain to head and emit contextual logs."""
    logger.info(
        "Running migration chain to head (chain=%s, schema=%s)",
        chain,
        schema or "<default>",
    )
    command.upgrade(config, f"{chain}@head")


def _bootstrap_extensions(db_url: str, *, connection=None) -> None:
    """Install required PostgreSQL extensions before running migrations.

    Uses ``CREATE EXTENSION IF NOT EXISTS`` so the call is idempotent and safe
    to run on every migration invocation.  Must be called **after** the target
    database exists and **before** any Alembic ``upgrade`` that references
    extension-provided types (e.g. ``vector``, ``tsvector``).
    """
    if connection is not None:
        for ext in ("vector", "pgcrypto", "uuid-ossp", "pg_trgm"):
            connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
        connection.commit()
        return
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for ext in ("vector", "pgcrypto", "uuid-ossp", "pg_trgm"):
                conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
    finally:
        engine.dispose()


async def run_migrations(
    db_url: str, chain: str = "core", schema: str | None = None, *, connection=None
) -> None:
    """Run Alembic migrations programmatically for a specific chain.

    This is the primary entry point for running migrations from the butler
    daemon. It configures Alembic to use the correct version directory and
    upgrades to the latest revision.

    Args:
        db_url: SQLAlchemy-compatible database URL
            (e.g. ``postgresql://user:pass@host:port/dbname``).
        chain: Version chain to migrate. Must be one of the recognized chains
            (core, approvals, or any butler name with a migrations/
            directory). Pass ``"all"`` to migrate all chains.
        schema: Optional target schema for one-db/multi-schema topology.
    """
    chains = _resolve_target_chains(chain)
    normalized_schema = _normalize_schema(schema)
    for resolved_chain in chains:
        if _resolve_chain_dir(resolved_chain) is None:
            raise ValueError(f"Unknown migration chain: {resolved_chain!r}")
    if connection is None:
        # Direct administrative callers own the same complete lifetime as the
        # multi-chain CLI. A raw Alembic connection is never a maintenance bypass.
        with migration_session(db_url) as held:
            await run_migrations(db_url, chain=chain, schema=normalized_schema, connection=held)
            finalize_authority(
                held, plans=tuple((name, normalized_schema, None) for name in chains)
            )
        return
    check_bootstrap_connection(connection)
    if "core" in chains:
        from butlers.db_authority.interfaces import stage_dashboard_interfaces

        stage_dashboard_interfaces(connection)

    # Extensions must exist before any migration that uses extension-provided
    # types (e.g. pgvector's ``vector``).  The call is idempotent.
    if "core" in chains or chain == "core":
        if connection is None:
            _bootstrap_extensions(db_url)
        else:
            _bootstrap_extensions(db_url, connection=connection)

    config = _build_alembic_config(db_url, chains, target_schema=normalized_schema)
    if connection is not None:
        config.attributes["connection"] = connection
    if connection is None:
        for resolved_chain in chains:
            _upgrade_chain(config, resolved_chain, normalized_schema)
    else:
        with ordinary_migration_identity(connection):
            for resolved_chain in chains:
                _upgrade_chain(config, resolved_chain, normalized_schema)


@contextmanager
def operator_rollback_session(db_url: str):
    """Explicit maintenance session for R0/R1, including the PRE-cutover owner.

    Ordinary migration admission deliberately rejects the old shared profile.
    This separately named administrative path proves actual superuser identity,
    owns the identical migration lock, and never becomes a serving connection.
    """
    from butlers.db_authority.apply import _rollback_actor

    engine = create_engine(
        db_url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=300000 -c lock_timeout=5000",
        },
    )
    connection = None
    locked = False
    primary = None
    try:
        connection = engine.connect()
        _rollback_actor(connection)
        connection.commit()
        deadline = time.monotonic() + 60
        while not locked:
            locked = (
                connection.execute(
                    text("SELECT pg_catalog.pg_try_advisory_lock(:family,:operation)"),
                    {"family": _MIGRATION_LOCK[0], "operation": _MIGRATION_LOCK[1]},
                ).scalar_one()
                is True
            )
            connection.commit()
            if not locked:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Operator rollback migration lock is unavailable")
                time.sleep(0.1)
        yield connection
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_error = None
        try:
            if connection is not None:
                connection.rollback()
                if locked:
                    released = connection.execute(
                        text("SELECT pg_catalog.pg_advisory_unlock(:family,:operation)"),
                        {"family": _MIGRATION_LOCK[0], "operation": _MIGRATION_LOCK[1]},
                    ).scalar_one()
                    if released is not True:
                        raise RuntimeError("Operator rollback lock release was incomplete")
                    connection.commit()
        except BaseException as exc:
            cleanup_error = exc
        for cleanup in (None if connection is None else connection.close, engine.dispose):
            if cleanup is None:
                continue
            try:
                cleanup()
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
                else:
                    cleanup_error.add_note("Operator rollback physical cleanup was incomplete")
        if cleanup_error is not None:
            if primary is None:
                raise cleanup_error
            primary.add_note("Operator rollback cleanup was incomplete")


def run_preserving_rollback(db_url: str, capsule: dict, *, transition: str):
    """Execute source-owned R0 or the actual core281→280 reverse on one session."""
    from butlers.db_authority.apply import (
        replay_predecessor_r0,
        restore_predecessor_r1,
        verify_predecessor_restored,
    )
    from butlers.db_authority.roles import AuthorityError

    if transition not in {"R0", "R1"}:
        raise AuthorityError("Operator rollback transition is unsupported")
    with operator_rollback_session(db_url) as held:
        if transition == "R0":
            receipt = replay_predecessor_r0(held, capsule)
        else:
            restore_predecessor_r1(held, capsule)
            for row in capsule["catalog"]["versions"]:
                if "core_280" not in row["revisions"]:
                    continue
                config = _build_alembic_config(db_url, ["core"], target_schema=row["schema"])
                config.attributes["connection"] = held
                command.downgrade(config, "core_280")
            receipt = verify_predecessor_restored(held, capsule)
        held.commit()
    # Nothing is reported as completed before commit AND actual session cleanup.
    return receipt
