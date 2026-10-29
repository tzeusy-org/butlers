"""Native SELECT-only convergence and rollback for the Home history reader."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = pytest.mark.integration


def test_home_history_grant_is_select_only_reversible_and_prerequisite_safe(
    postgres_container,
    monkeypatch,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core_282")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            # Current bootstrap defaults can already grant SELECT. Deliberately
            # remove only this test database's table grant to expose convergence.
            assert isinstance(
                connection.execute(
                    text(
                        "SELECT has_table_privilege('butler_home_rw', "
                        "'connectors.home_assistant_history', 'SELECT')"
                    )
                ).scalar_one(),
                bool,
            )
            connection.execute(
                text("REVOKE SELECT ON connectors.home_assistant_history FROM butler_home_rw")
            )
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege('butler_home_rw', "
                        "'connectors.home_assistant_history', 'SELECT')"
                    )
                ).scalar_one()
                is False
            )
            identity = connection.execute(
                text(
                    "INSERT INTO connectors.home_assistant_history "
                    "(entity_id, state, recorded_at) VALUES ('sensor.history_test', 'on', now()) "
                    "RETURNING id"
                )
            ).scalar_one()
            oid = connection.execute(
                text("SELECT 'connectors.home_assistant_history'::regclass::oid")
            ).scalar_one()
        command.upgrade(config, "core_289")
        with engine.begin() as connection:
            privileges = (
                connection.execute(
                    text(
                        "SELECT has_table_privilege('butler_home_rw', "
                        "'connectors.home_assistant_history', 'SELECT') AS read, "
                        "has_table_privilege('butler_home_rw', "
                        "'connectors.home_assistant_history', 'INSERT,UPDATE,DELETE') AS write, "
                        "has_schema_privilege('butler_home_rw', 'connectors', 'USAGE') AS usage"
                    )
                )
                .mappings()
                .one()
            )
            assert dict(privileges) == {"read": True, "write": False, "usage": True}
            connection.execute(text("SET LOCAL ROLE butler_home_rw"))
            assert connection.execute(text("SELECT current_user")).scalar_one() == "butler_home_rw"
            assert (
                connection.execute(
                    text("SELECT state FROM connectors.home_assistant_history WHERE id = :id"),
                    {"id": identity},
                ).scalar_one()
                == "on"
            )
            with connection.begin_nested() as blocked:
                with pytest.raises(ProgrammingError, match="permission denied"):
                    connection.execute(
                        text(
                            "INSERT INTO connectors.home_assistant_history "
                            "(entity_id, state, recorded_at) VALUES ('sensor.forbidden', 'on', now())"
                        )
                    )
                blocked.rollback()
        command.downgrade(config, "core_282")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege('butler_home_rw', "
                        "'connectors.home_assistant_history', 'SELECT')"
                    )
                ).scalar_one()
                is False
            )
            assert (
                connection.execute(
                    text("SELECT 'connectors.home_assistant_history'::regclass::oid")
                ).scalar_one()
                == oid
            )
            assert (
                connection.execute(
                    text("SELECT state FROM connectors.home_assistant_history WHERE id = :id"),
                    {"id": identity},
                ).scalar_one()
                == "on"
            )
        command.upgrade(config, "core_289")
        path = (
            Path(__file__).resolve().parents[2]
            / "alembic/versions/core"
            / "core_289_home_recorded_history_read_grant.py"
        )
        spec = importlib.util.spec_from_file_location("home_recorded_history_grant", path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with (
            engine.begin() as connection,
            Operations.context(MigrationContext.configure(connection)),
        ):
            # Same real migration functions must safely skip a missing target;
            # no global role is dropped or renamed on a shared test server.
            with monkeypatch.context() as patch:
                patch.setattr(migration, "_ROLE", "home_history_absent_role")
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM pg_roles WHERE rolname = 'home_history_absent_role'"
                        )
                    ).scalar_one()
                    == 0
                )
                migration.upgrade()
                migration.downgrade()
            with connection.begin_nested() as absent_table:
                connection.execute(
                    text(
                        "ALTER TABLE connectors.home_assistant_history RENAME TO history_test_absent"
                    )
                )
                migration.upgrade()
                migration.downgrade()
                absent_table.rollback()
            assert (
                connection.execute(
                    text(
                        "SELECT has_table_privilege('butler_home_rw', "
                        "'connectors.home_assistant_history', 'SELECT')"
                    )
                ).scalar_one()
                is True
            )
    finally:
        engine.dispose()
