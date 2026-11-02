"""core_307: ``public.consolidation_runs`` is append-only for runtime roles (bu-7kbtfw).

Runs the real core chain, so the bootstrap default privileges that core_119's
SELECT-only grant never revoked are in play.  The legitimate writer is the
butler daemon's memory module, which inserts one audit row per consolidated
group as its own ``butler_<name>_rw`` role.
"""

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

_BUTLER_ROLES = ("butler_general_rw", "butler_qa_rw")
_CONNECTOR_ROLE = "connector_writer"
_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
_INSERT = "INSERT INTO public.consolidation_runs (butler, facts_produced) VALUES ('general', 3)"
_REWRITES = (
    "UPDATE public.consolidation_runs SET facts_produced = 99",
    "DELETE FROM public.consolidation_runs",
    "TRUNCATE public.consolidation_runs",
)


def _privileges(connection) -> dict[str, set[str]]:
    """Table privileges each runtime role holds on the audit table, from the catalog."""
    return {
        role: {
            privilege
            for privilege in _PRIVILEGES
            if connection.execute(
                text("SELECT has_table_privilege(:role, 'public.consolidation_runs', :p)"),
                {"role": role, "p": privilege},
            ).scalar_one()
        }
        for role in (*_BUTLER_ROLES, _CONNECTOR_ROLE)
    }


_APPEND_ONLY = {
    **{role: {"SELECT", "INSERT"} for role in _BUTLER_ROLES},
    _CONNECTOR_ROLE: {"SELECT"},
}
_BOOTSTRAP_DEFAULTS = {
    role: {"SELECT", "INSERT", "UPDATE", "DELETE"} for role in (*_BUTLER_ROLES, _CONNECTOR_ROLE)
}


def _assert_denied(connection, statement: str) -> None:
    with connection.begin_nested() as blocked:
        with pytest.raises(ProgrammingError, match="permission denied"):
            connection.execute(text(statement))
        blocked.rollback()


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


def test_consolidation_runs_is_append_only_replay_safe_and_reversible(postgres_container) -> None:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core_306")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            # The gap: bootstrap default privileges outlive core_119's SELECT-only grant.
            assert _privileges(connection) == _BOOTSTRAP_DEFAULTS

        command.upgrade(config, "core_307")
        with engine.begin() as connection:
            assert _privileges(connection) == _APPEND_ONLY
            # The real writer (consolidation's audit insert) keeps working as a butler
            # role, BIGSERIAL default included; it cannot rewrite or erase history.
            connection.execute(text('SET LOCAL ROLE "butler_general_rw"'))
            connection.execute(text(_INSERT))
            assert connection.execute(
                text("SELECT facts_produced FROM public.consolidation_runs")
            ).scalar_one() == 3
            for statement in _REWRITES:
                _assert_denied(connection, statement)
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{_CONNECTOR_ROLE}"'))
            _assert_denied(connection, _INSERT)

        _rerun_init_db(migration_bootstrap_db_url(postgres_container, db_name), db_url)
        with engine.connect() as connection:
            assert _privileges(connection) == _APPEND_ONLY

        command.downgrade(config, "core_306")
        with engine.connect() as connection:
            assert _privileges(connection) == _BOOTSTRAP_DEFAULTS
            assert connection.execute(
                text("SELECT count(*) FROM public.consolidation_runs")
            ).scalar_one() == 1
    finally:
        engine.dispose()
