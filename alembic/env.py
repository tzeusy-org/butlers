"""Alembic environment for multi-chain butler migrations.

Supports:
- Programmatic invocation from the daemon (not just CLI)
- Targeting a specific butler's database via connection URL
- Multiple version chains (core + module + butler-specific)
- Raw SQL via op.execute() (no SQLAlchemy models)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from alembic.script import ScriptDirectory

from alembic import context
from butlers.bootstrap_prerequisite import check_bootstrap_connection

# The directory containing shared version chain subdirectories (core only)
VERSIONS_DIR = Path(__file__).parent / "versions"

# The directory containing butler config dirs (each may have a migrations/ folder)
ROSTER_DIR = Path(__file__).parent.parent / "roster"

# The directory containing module packages (each may have a migrations/ folder)
MODULES_DIR = Path(__file__).parent.parent / "src" / "butlers" / "modules"

# Shared chains that live in alembic/versions/ (core only)
_SHARED_CHAINS = ["core"]
_TARGET_SCHEMA_OPTION = "butlers.target_schema"
_VERSION_TABLE_SCHEMA_OPTION = "version_table_schema"
_VALID_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _discover_module_chains() -> list[str]:
    """Discover module-local migration chains from module directories.

    Scans ``src/butlers/modules/*/migrations/`` for directories that contain
    at least one ``.py`` migration file (excluding ``__init__.py``).
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


def get_url() -> str:
    """Resolve the database URL from context or environment."""
    url = context.config.get_main_option("sqlalchemy.url")
    if url:
        return url
    return os.environ.get(
        "BUTLERS_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/butlers",
    )


def _quote_ident(identifier: str) -> str:
    """Quote a SQL identifier for safe interpolation."""
    return '"' + identifier.replace('"', '""') + '"'


def _get_config_schema_option(option_name: str) -> str | None:
    """Read and validate a schema option from Alembic config."""
    raw = context.config.get_main_option(option_name)
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    if _VALID_SCHEMA_RE.fullmatch(normalized) is None:
        raise ValueError(f"Invalid schema option {option_name}: {raw!r}")
    return normalized


def get_version_locations() -> list[str]:
    """Build the version_locations list for all chains.

    Includes shared chains from alembic/versions/, module chains from
    src/butlers/modules/*/migrations/, and butler-specific chains from
    roster/<name>/migrations/.
    """
    locations = []

    # Shared chains (core) in alembic/versions/
    for chain in _SHARED_CHAINS:
        chain_dir = VERSIONS_DIR / chain
        if chain_dir.is_dir():
            locations.append(str(chain_dir))

    # Module-local chains in src/butlers/modules/<name>/migrations/
    for module_name in _discover_module_chains():
        mig_dir = MODULES_DIR / module_name / "migrations"
        locations.append(str(mig_dir))

    # Butler-specific chains in roster/<name>/migrations/
    for butler_name in _discover_butler_chains():
        mig_dir = ROSTER_DIR / butler_name / "migrations"
        locations.append(str(mig_dir))

    return locations


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = get_url()
    version_table_schema = _get_config_schema_option(_VERSION_TABLE_SCHEMA_OPTION)
    configure_kwargs = {
        "url": url,
        "target_metadata": None,
        "literal_binds": True,
        "dialect_opts": {"paramstyle": "named"},
        "version_locations": get_version_locations(),
    }
    if version_table_schema is not None:
        configure_kwargs["version_table_schema"] = version_table_schema
    context.configure(**configure_kwargs)

    with context.begin_transaction():
        context.run_migrations()


def _require_preserving_direction(connection) -> None:
    """Refuse a core282 reverse before ordinary schema setup can commit.

    Alembic invokes revision downgrade hooks only after its environment has
    run. Read the actual selected version table first; neither a privileged
    identity nor possession of the migration lock authorizes an unprepared
    reverse. Older administrative targets retain their historical behavior.
    """
    from alembic.util import CommandError
    from sqlalchemy import text

    from butlers.db_authority.apply import require_preserving_reverse
    from butlers.db_authority.roles import AuthorityError

    destination = context.get_revision_argument()
    if destination is not None and not isinstance(destination, (str, tuple)):
        raise AuthorityError("Migration destination is not source-owned")
    schema = _get_config_schema_option(_VERSION_TABLE_SCHEMA_OPTION) or "public"
    relation = _quote_ident(schema) + ".alembic_version"
    if (
        connection.execute(
            text("SELECT pg_catalog.to_regclass(:relation)"), {"relation": relation}
        ).scalar_one()
        is None
    ):
        return
    revisions = connection.exec_driver_sql("SELECT version_num FROM " + relation).scalars().all()
    if "core_282" not in revisions:
        return
    script = ScriptDirectory.from_config(context.config)
    try:
        # Use the same pinned Alembic graph resolver as command.downgrade.
        # as_revision_number leaves relative targets such as -1 unresolved;
        # comparing destination strings would therefore miss a real reverse.
        steps = script._downgrade_revs(destination, tuple(revisions))
    except CommandError:
        # A legitimate forward operation on a sibling branch is not a reverse.
        # Invalid/ambiguous destinations still raise before ordinary setup.
        script._upgrade_revs(destination, tuple(revisions))
        return
    if any(step.revision.revision == "core_282" for step in steps):
        if connection.info.get("butlers.preserving_reverse") is None:
            raise AuthorityError("Explicit preserving operator reverse is required")
        require_preserving_reverse(connection)


def _run_on_connection(connection) -> None:
    """Run on the actual caller-owned session, including schema/version setup."""
    from butlers.migrations import ordinary_migration_identity

    _require_preserving_direction(connection)
    if connection.info.get("butlers.preserving_reverse") is not None:
        from butlers.db_authority.apply import quote_identifier, require_preserving_reverse

        require_preserving_reverse(connection)
        schema = _get_config_schema_option(_TARGET_SCHEMA_OPTION)
        if schema is None:
            raise RuntimeError("Preserving reverse requires its captured core schema")
        connection.exec_driver_sql(f"SET LOCAL search_path TO {quote_identifier(schema)},public")
        context.configure(
            connection=connection,
            target_metadata=None,
            version_locations=get_version_locations(),
            version_table_schema=schema,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    check_bootstrap_connection(connection)
    connection.rollback()
    with ordinary_migration_identity(connection):
        _run_admitted_chain(connection)


def _run_admitted_chain(connection) -> None:
    """Every ordinary object and version table is created as its normal owner."""
    target_schema = _get_config_schema_option(_TARGET_SCHEMA_OPTION)
    version_table_schema = _get_config_schema_option(_VERSION_TABLE_SCHEMA_OPTION)
    if target_schema is not None:
        own_schema = _quote_ident(target_schema)
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {own_schema}")
        connection.exec_driver_sql(f"SET search_path TO {own_schema}, public")
    connection.commit()
    configure_kwargs = {
        "connection": connection,
        "target_metadata": None,
        "version_locations": get_version_locations(),
    }
    if version_table_schema is not None:
        configure_kwargs["version_table_schema"] = version_table_schema
    context.configure(**configure_kwargs)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Use the CLI's lock-owning session rather than reconnecting per chain."""
    held = context.config.attributes.get("connection")
    if held is not None:
        _run_on_connection(held)
        return
    from butlers.db_authority.interfaces import stage_dashboard_interfaces
    from butlers.db_authority.roles import AuthorityError
    from butlers.migrations import (
        _bootstrap_extensions,
        finalize_authority,
        get_all_chains,
        get_chain_head,
        get_chain_revision_ids,
        get_chain_revision_path,
        migration_session,
    )

    # Resolve the actual requested graph before opening a connection. An empty
    # or ambiguous target is not permission to replay every deployment chain.
    script = ScriptDirectory.from_config(context.config)
    revisions = script.get_revisions(context.get_revision_argument())
    plans = []
    target_schema = _get_config_schema_option(_TARGET_SCHEMA_OPTION)
    for revision in revisions:
        chains = [
            chain
            for chain in get_all_chains()
            if revision.revision in get_chain_revision_ids(chain)
        ]
        if len(chains) != 1:
            raise AuthorityError("Selected migration target is not source-owned")
        chain = chains[0]
        selected = None if revision.revision == get_chain_head(chain) else revision.revision
        plans.append((chain, target_schema, selected))
    if not plans:
        raise AuthorityError("Destructive migration requires the explicit rollback procedure")
    with migration_session(get_url()) as connection:
        _require_preserving_direction(connection)
        if any(chain == "core" for chain, _, _ in plans):
            # A bounded administrative replay cannot install a later purpose
            # boundary. Follow the actual chain graph, as the ordinary selected
            # producer does, rather than treating every core target as head.
            if any(
                chain == "core"
                and {"core_277", "core_278"} & get_chain_revision_path(chain, revision)
                for chain, _, revision in plans
            ):
                stage_dashboard_interfaces(connection)
            _bootstrap_extensions(get_url(), connection=connection)
        _run_on_connection(connection)
        finalize_authority(connection, plans=tuple(plans))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
