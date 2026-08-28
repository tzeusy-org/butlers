"""Real PostgreSQL regression coverage for the Health-to-Chronicler read grant."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.migrations import run_migrations
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/butlers/modules/memory/migrations/011_grant_chronicler_health_facts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("mem_011", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(connection: Connection, module, direction: str, schema: str) -> None:
    connection.execute(text(f'SET search_path TO "{schema}", public'))
    operations = Operations(MigrationContext.configure(connection))
    with patch.object(module, "op", operations):
        getattr(module, direction)()


def test_fresh_core_then_health_memory_chain_grants_chronicler_select(
    postgres_container,
) -> None:
    """The grant must be applied after the Health memory chain creates facts.

    Core runs before the module chain during daemon startup. A core/bootstrap
    grant therefore cannot repair a fresh Health deployment because
    ``health.facts`` does not exist until ``mem_001`` runs.
    """
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT to_regclass('health.facts')")).scalar_one() is None
            )
    finally:
        engine.dispose()

    asyncio.run(run_migrations(db_url, chain="memory", schema="health"))

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'butler_chronicler_rw', 'health.facts', 'SELECT'"
                        ")"
                    )
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()


def test_memory_chain_does_not_grant_chronicler_select_in_non_health_schema(
    postgres_container,
) -> None:
    """The read surface is limited to the Health memory schema."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="finance"))
    asyncio.run(run_migrations(db_url, chain="memory", schema="finance"))

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'butler_chronicler_rw', 'finance.facts', 'SELECT'"
                        ")"
                    )
                ).scalar_one()
                is False
            )
    finally:
        engine.dispose()


def test_health_migration_noops_when_facts_table_is_absent(postgres_container) -> None:
    """A partial Health deployment must not make either migration direction fail."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    module = _load_migration()
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS health"))
            _run_migration(connection, module, "upgrade", "health")
            _run_migration(connection, module, "downgrade", "health")
    finally:
        engine.dispose()


def test_health_migration_noops_when_chronicler_role_is_absent(postgres_container) -> None:
    """A partial role bootstrap must not grant a different principal by accident."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    module = _load_migration()
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE health.facts (id INTEGER PRIMARY KEY)"))
            connection.execute(
                text("REVOKE SELECT ON TABLE health.facts FROM butler_chronicler_rw")
            )
            with patch.object(module, "_CHRONICLER_ROLE", "missing_chronicler_role"):
                _run_migration(connection, module, "upgrade", "health")

            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'butler_chronicler_rw', 'health.facts', 'SELECT'"
                        ")"
                    )
                ).scalar_one()
                is False
            )
    finally:
        engine.dispose()


def test_health_grant_is_select_only_usable_by_role_and_downgrade_is_scoped(
    postgres_container,
) -> None:
    """The migration grants one usable read surface and removes only that grant."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    module = _load_migration()

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE health.facts (id INTEGER PRIMARY KEY)"))
            connection.execute(text("GRANT USAGE ON SCHEMA health TO butler_chronicler_rw"))
            _run_migration(connection, module, "upgrade", "health")
            _run_migration(connection, module, "upgrade", "health")

            privileges = (
                connection.execute(
                    text(
                        "SELECT "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'SELECT') "
                        "AS can_select, "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'INSERT') "
                        "AS can_insert, "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'UPDATE') "
                        "AS can_update, "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'DELETE') "
                        "AS can_delete, "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'TRUNCATE') "
                        "AS can_truncate, "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'REFERENCES') "
                        "AS can_references, "
                        "has_table_privilege('butler_chronicler_rw', 'health.facts', 'TRIGGER') "
                        "AS can_trigger"
                    )
                )
                .mappings()
                .one()
            )
            assert privileges == {
                "can_select": True,
                "can_insert": False,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
                "can_references": False,
                "can_trigger": False,
            }
    finally:
        engine.dispose()

    admin_engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text('SET ROLE "butler_chronicler_rw"'))
            try:
                assert (
                    connection.execute(text("SELECT count(*) FROM health.facts")).scalar_one() == 0
                )
                for statement in (
                    "INSERT INTO health.facts (id) VALUES (1)",
                    "TRUNCATE TABLE health.facts",
                ):
                    with pytest.raises(ProgrammingError, match="permission denied"):
                        connection.execute(text(statement))
            finally:
                connection.execute(text("RESET ROLE"))
    finally:
        admin_engine.dispose()

    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("GRANT INSERT ON TABLE health.facts TO butler_chronicler_rw"))
            _run_migration(connection, module, "downgrade", "health")
            _run_migration(connection, module, "downgrade", "health")

            assert (
                connection.execute(
                    text(
                        "SELECT NOT has_table_privilege("
                        "'butler_chronicler_rw', 'health.facts', 'SELECT'"
                        ") AND has_table_privilege("
                        "'butler_chronicler_rw', 'health.facts', 'INSERT'"
                        ")"
                    )
                ).scalar_one()
                is True
            )

            connection.execute(text("CREATE SCHEMA IF NOT EXISTS finance"))
            connection.execute(text("CREATE TABLE finance.facts (id INTEGER PRIMARY KEY)"))
            connection.execute(text("GRANT SELECT ON TABLE finance.facts TO butler_chronicler_rw"))
            _run_migration(connection, module, "downgrade", "finance")

            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'butler_chronicler_rw', 'finance.facts', 'SELECT'"
                        ")"
                    )
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()

    admin_engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            connection.execute(text('SET ROLE "butler_chronicler_rw"'))
            try:
                with pytest.raises(ProgrammingError, match="permission denied"):
                    connection.execute(text("SELECT count(*) FROM health.facts"))
                assert (
                    connection.execute(text("INSERT INTO health.facts (id) VALUES (1)")).rowcount
                    == 1
                )
            finally:
                connection.execute(text("RESET ROLE"))
    finally:
        admin_engine.dispose()
