"""Provisioning-path parity for ``runtime_attention_admin.bootstrap_configuration``.

``scripts/init-db.sql`` provisions the runtime-attention bootstrap table two
ways: a ``CREATE TABLE IF NOT EXISTS`` for a fresh database, and an
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` for a database that predates the
``interface_version`` column. Both paths must leave the same constraint behind,
otherwise upgraded deployments silently lose the version guard that
``finalize_interface`` assumes.

These proofs run the real script against a disposable PostgreSQL container and
read the resulting constraint back out of ``pg_constraint``; a source-only
comparison would not answer the question.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

docker_available = shutil.which("docker") is not None
psql_available = shutil.which("psql") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.skipif(not psql_available, reason="psql not available"),
]

_INIT_DB = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"
_MIGRATION_ROLE = "butlers"
_FRESH_DB = "init_db_iface_created"
_UPGRADED_DB = "init_db_iface_upgraded"
_ALREADY_UPGRADED_DB = "init_db_iface_already_upgraded"

# The shape of the table before ``interface_version`` existed, reproduced so the
# ADD COLUMN branch of init-db.sql is the code path that installs the column.
_LEGACY_TABLE_DDL = """
CREATE TABLE runtime_attention_admin.bootstrap_configuration (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    migration_role NAME NOT NULL,
    bootstrap_role NAME NOT NULL
)
"""

# The ALTER exactly as init-db.sql carried it before this fix. Replaying it over
# the legacy table reproduces the deployed population the fix has to converge:
# the column is present and unconstrained, so ADD COLUMN IF NOT EXISTS will skip
# its whole subcommand — CHECK included — on every later run.
_PRE_FIX_ADD_COLUMN_DDL = """
ALTER TABLE runtime_attention_admin.bootstrap_configuration
    ADD COLUMN IF NOT EXISTS interface_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS producers_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS producer_activated_at TIMESTAMPTZ
"""

_CHECK_CONSTRAINTS_SQL = """
SELECT constraint_.conname AS name,
       pg_get_constraintdef(constraint_.oid) AS definition
FROM pg_constraint AS constraint_
JOIN pg_class AS relation ON relation.oid = constraint_.conrelid
JOIN pg_namespace AS admin_schema ON admin_schema.oid = relation.relnamespace
JOIN LATERAL unnest(constraint_.conkey) AS constrained_column(attnum) ON true
JOIN pg_attribute AS attribute
  ON attribute.attrelid = relation.oid
 AND attribute.attnum = constrained_column.attnum
WHERE admin_schema.nspname = 'runtime_attention_admin'
  AND relation.relname = 'bootstrap_configuration'
  AND constraint_.contype = 'c'
  AND attribute.attname = 'interface_version'
ORDER BY constraint_.conname
"""


@pytest.fixture(scope="module")
def postgres_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


def _admin_params(postgres_container) -> tuple[str, str, str, str]:
    return (
        postgres_container.get_container_host_ip(),
        str(postgres_container.get_exposed_port(5432)),
        postgres_container.username,
        postgres_container.password,
    )


def _run_init_db(*, host: str, port: str, user: str, password: str, database: str) -> None:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    subprocess.run(
        [
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            database,
            "-f",
            str(_INIT_DB),
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def _provision(postgres_container, *, database: str, staged_ddl: tuple[str, ...] = ()) -> str:
    """Run init-db.sql against a fresh database and return its admin URL.

    ``staged_ddl`` runs as the bootstrap superuser before init-db.sql, creating
    the admin schema and whatever earlier-generation table shape the caller
    wants the script to encounter.
    """
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    control_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {database}"))
            if not conn.execute(
                text("SELECT true FROM pg_roles WHERE rolname = :role"),
                {"role": _MIGRATION_ROLE},
            ).scalar():
                conn.execute(
                    text(f"CREATE ROLE {_MIGRATION_ROLE} LOGIN PASSWORD '{_MIGRATION_ROLE}'")
                )
            conn.execute(text(f"CREATE DATABASE {database} OWNER {_MIGRATION_ROLE}"))
    finally:
        engine.dispose()

    database_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/{database}"
    if staged_ddl:
        engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(
                    text(f"CREATE SCHEMA runtime_attention_admin AUTHORIZATION {admin_user}")
                )
                for statement in staged_ddl:
                    conn.execute(text(statement))
        finally:
            engine.dispose()

    _run_init_db(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database=database,
    )
    return database_url


def _interface_version_checks(database_url: str) -> list[tuple[str, str]]:
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(_CHECK_CONSTRAINTS_SQL)).mappings().all()
    finally:
        engine.dispose()
    return [(row["name"], row["definition"]) for row in rows]


@pytest.fixture(scope="module")
def created_path_url(postgres_container) -> str:
    return _provision(postgres_container, database=_FRESH_DB)


@pytest.fixture(scope="module")
def upgraded_path_url(postgres_container) -> str:
    return _provision(postgres_container, database=_UPGRADED_DB, staged_ddl=(_LEGACY_TABLE_DDL,))


@pytest.fixture(scope="module")
def already_upgraded_path_url(postgres_container) -> str:
    """A database a pre-fix init-db.sql already carried through its ADD COLUMN."""
    return _provision(
        postgres_container,
        database=_ALREADY_UPGRADED_DB,
        staged_ddl=(_LEGACY_TABLE_DDL, _PRE_FIX_ADD_COLUMN_DDL),
    )


def test_upgrade_branch_installed_the_interface_version_column(upgraded_path_url: str) -> None:
    """The staged legacy table really did reach the ADD COLUMN branch."""
    engine = create_engine(upgraded_path_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            column_exists = conn.execute(
                text(
                    "SELECT true FROM information_schema.columns "
                    "WHERE table_schema = 'runtime_attention_admin' "
                    "AND table_name = 'bootstrap_configuration' "
                    "AND column_name = 'interface_version'"
                )
            ).scalar()
            producers_enabled_default = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = 'runtime_attention_admin' "
                    "AND table_name = 'bootstrap_configuration' "
                    "AND column_name = 'producers_enabled'"
                )
            ).scalar()
    finally:
        engine.dispose()

    assert column_exists is True
    assert producers_enabled_default == "false"


def test_interface_version_check_is_identical_across_provisioning_paths(
    created_path_url: str, upgraded_path_url: str
) -> None:
    """CREATE TABLE and ADD COLUMN leave the same constraint on the column."""
    created_checks = _interface_version_checks(created_path_url)
    upgraded_checks = _interface_version_checks(upgraded_path_url)

    assert created_checks == [
        (
            "bootstrap_configuration_interface_version_check",
            "CHECK ((interface_version = ANY (ARRAY[1, 2])))",
        )
    ]
    assert upgraded_checks == created_checks


@pytest.mark.parametrize("path", ["created", "upgraded"])
def test_unsupported_interface_version_is_rejected_by_the_database(
    path: str, created_path_url: str, upgraded_path_url: str
) -> None:
    """Neither provisioning path relies on finalize_interface as the only guard."""
    database_url = created_path_url if path == "created" else upgraded_path_url
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            updated = conn.execute(
                text(
                    "UPDATE runtime_attention_admin.bootstrap_configuration "
                    "SET interface_version = interface_version"
                )
            ).rowcount
            assert updated == 1, "bootstrap configuration row is missing"
            with pytest.raises(IntegrityError) as excinfo:
                conn.execute(
                    text(
                        "UPDATE runtime_attention_admin.bootstrap_configuration "
                        "SET interface_version = 3"
                    )
                )
    finally:
        engine.dispose()

    assert "bootstrap_configuration_interface_version_check" in str(excinfo.value)


def test_rerunning_init_db_does_not_duplicate_the_interface_version_check(
    postgres_container, upgraded_path_url: str
) -> None:
    """init-db.sql is re-run on every stack start; the ALTER must stay idempotent."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    _run_init_db(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database=_UPGRADED_DB,
    )

    assert _interface_version_checks(upgraded_path_url) == [
        (
            "bootstrap_configuration_interface_version_check",
            "CHECK ((interface_version = ANY (ARRAY[1, 2])))",
        )
    ]


def test_stage_reproduces_an_unconstrained_deployed_database(postgres_container) -> None:
    """The pre-fix staging really does leave the column present and unguarded."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    database = f"{_ALREADY_UPGRADED_DB}_stage"
    control_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/postgres"
    engine = create_engine(control_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {database}"))
            conn.execute(text(f"CREATE DATABASE {database}"))
    finally:
        engine.dispose()

    database_url = f"postgresql://{admin_user}:{admin_password}@{host}:{port}/{database}"
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"CREATE SCHEMA runtime_attention_admin AUTHORIZATION {admin_user}"))
            conn.execute(text(_LEGACY_TABLE_DDL))
            conn.execute(text(_PRE_FIX_ADD_COLUMN_DDL))
            column_exists = conn.execute(
                text(
                    "SELECT true FROM information_schema.columns "
                    "WHERE table_schema = 'runtime_attention_admin' "
                    "AND table_name = 'bootstrap_configuration' "
                    "AND column_name = 'interface_version'"
                )
            ).scalar()
    finally:
        engine.dispose()

    assert column_exists is True
    assert _interface_version_checks(database_url) == []


def test_already_upgraded_database_gains_the_interface_version_check(
    already_upgraded_path_url: str, created_path_url: str
) -> None:
    """The deployed population the bead names converges on the same constraint.

    ``ADD COLUMN IF NOT EXISTS`` skips its entire subcommand once the column
    exists, so these databases can only be repaired by an explicit constraint
    installation.
    """
    assert _interface_version_checks(already_upgraded_path_url) == _interface_version_checks(
        created_path_url
    )


def test_already_upgraded_database_rejects_unsupported_interface_version(
    already_upgraded_path_url: str,
) -> None:
    """The repaired constraint is enforced, not merely present in the catalog."""
    engine = create_engine(already_upgraded_path_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            with pytest.raises(IntegrityError) as excinfo:
                conn.execute(
                    text(
                        "UPDATE runtime_attention_admin.bootstrap_configuration "
                        "SET interface_version = 3"
                    )
                )
    finally:
        engine.dispose()

    assert "bootstrap_configuration_interface_version_check" in str(excinfo.value)


def test_rerunning_init_db_on_a_repaired_database_stays_idempotent(
    postgres_container, already_upgraded_path_url: str
) -> None:
    """There is no ADD CONSTRAINT IF NOT EXISTS; the repair must guard itself."""
    host, port, admin_user, admin_password = _admin_params(postgres_container)
    _run_init_db(
        host=host,
        port=port,
        user=admin_user,
        password=admin_password,
        database=_ALREADY_UPGRADED_DB,
    )

    assert _interface_version_checks(already_upgraded_path_url) == [
        (
            "bootstrap_configuration_interface_version_check",
            "CHECK ((interface_version = ANY (ARRAY[1, 2])))",
        )
    ]
