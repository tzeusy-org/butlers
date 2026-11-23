"""core_306: runtime roles read ``public.state`` but cannot write it (bu-0zm6zg)."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration

_ROLES = ("butler_general_rw", "butler_qa_rw", "connector_writer")
_WRITES = (
    "INSERT INTO public.state (key, value) VALUES ('forged', '{}'::jsonb)",
    "UPDATE public.state SET value = '{\"forged\": true}'::jsonb WHERE key = 'probe'",
    "DELETE FROM public.state WHERE key = 'probe'",
)


def _privileges(connection) -> dict[str, tuple[bool, bool]]:
    """(SELECT, any-DML) per runtime role, from the catalog."""
    return {
        role: tuple(
            connection.execute(
                text(
                    "SELECT has_table_privilege(:role, 'public.state', 'SELECT'), "
                    "has_table_privilege(:role, 'public.state', 'INSERT,UPDATE,DELETE')"
                ),
                {"role": role},
            ).one()
        )
        for role in _ROLES
    }


def _rerun_init_db(bootstrap_url: str, migration_url: str) -> None:
    """Replay the checked-in bootstrap, broad public grants included."""
    engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    raw = engine.raw_connection()
    try:
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (unquote(urlparse(migration_url).username or ""),),
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw.close()
        engine.dispose()


def test_public_state_is_runtime_read_only_replay_safe_and_reversible(postgres_container) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core_305")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            # Baseline: bootstrap default privileges hand runtime roles public DML.
            assert _privileges(connection) == {role: (True, True) for role in _ROLES}
            connection.execute(
                text("INSERT INTO public.state (key, value) VALUES ('probe', '{\"v\": 1}'::jsonb)")
            )

        command.upgrade(config, "core_306")
        with engine.begin() as connection:
            assert _privileges(connection) == {role: (True, False) for role in _ROLES}
            for role in _ROLES:
                with connection.begin_nested() as as_role:
                    connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                    assert connection.execute(
                        text("SELECT value FROM public.state WHERE key = 'probe'")
                    ).scalar_one() == {"v": 1}
                    for statement in _WRITES:
                        with connection.begin_nested() as blocked:
                            with pytest.raises(ProgrammingError, match="permission denied"):
                                connection.execute(text(statement))
                            blocked.rollback()
                    as_role.rollback()
            # The owner login (the dashboard API's identity) keeps writing.
            connection.execute(
                text("UPDATE public.state SET value = '{\"v\": 2}'::jsonb WHERE key = 'probe'")
            )

        # A bootstrap replay re-runs the broad public grant, then the revoke.
        _rerun_init_db(migration_bootstrap_db_url(postgres_container, db_name), db_url)
        with engine.connect() as connection:
            assert _privileges(connection) == {role: (True, False) for role in _ROLES}

        command.downgrade(config, "core_305")
        with engine.connect() as connection:
            assert _privileges(connection) == {role: (True, True) for role in _ROLES}
            assert connection.execute(
                text("SELECT value FROM public.state WHERE key = 'probe'")
            ).scalar_one() == {"v": 2}
    finally:
        engine.dispose()
