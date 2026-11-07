"""Bootstrap admission plans its catalog predicates under a bounded search (bu-rvgzmf.6).

The finalized-interface predicates are hundreds of lines of catalog joins; planning
them dominated each daemon boot. Admission now bounds the planner's join search with
``SET LOCAL`` inside a savepoint it rolls back, so the bound reaches no statement
after admission. Planner settings choose a plan, never a result: every check and
every refusal stays as it was, which the refusal cases below pin on real PostgreSQL.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from butlers import bootstrap_prerequisite as prereq
from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError, check_bootstrap_connection
from butlers.testing.migration import migration_bootstrap_db_url

_BOUNDS = ("join_collapse_limit", "from_collapse_limit")


# --------------------------------------------------------------------------- #
# Statement order, on a recording connection
# --------------------------------------------------------------------------- #


def _recording_connection(log: list[str]) -> MagicMock:
    connection = MagicMock()

    def execute(statement, params=None):
        log.append(str(statement))
        result = MagicMock()
        if "rolsuper FROM" in str(statement):
            result.one.return_value = ("butlers", False)
        return result

    def begin_nested():
        log.append("SAVEPOINT")
        savepoint = MagicMock()
        savepoint.rollback.side_effect = lambda: log.append("ROLLBACK TO SAVEPOINT")
        return savepoint

    connection.execute.side_effect = execute
    connection.begin_nested.side_effect = begin_nested
    return connection


@pytest.mark.parametrize("profile_holds", [True, False])
def test_bounds_are_set_locally_inside_a_savepoint_that_is_rolled_back(
    monkeypatch, profile_holds
) -> None:
    log: list[str] = []

    def fake_true(_connection, sql, params=None):
        log.append("CHECK")
        return profile_holds if sql is prereq._BASE_PROFILE else True

    monkeypatch.setattr(prereq, "_true", fake_true)
    connection = _recording_connection(log)
    if profile_holds:
        check_bootstrap_connection(connection)
    else:
        with pytest.raises(BootstrapPrerequisiteError, match="missing or untrusted"):
            check_bootstrap_connection(connection)

    assert log[0] == "SAVEPOINT"
    assert log[1:3] == [f"SET LOCAL {name} = 1" for name in _BOUNDS]
    assert "CHECK" in log and log.index("CHECK") > 2
    # Success and refusal alike end by discarding the savepoint, and with it the bounds.
    assert log[-1] == "ROLLBACK TO SAVEPOINT"


# --------------------------------------------------------------------------- #
# Real PostgreSQL
# --------------------------------------------------------------------------- #


def _admin_url(postgres_container, db_url: str) -> str:
    return migration_bootstrap_db_url(postgres_container, urlparse(db_url).path.lstrip("/"))


def _migration_role(connection) -> str:
    return connection.execute(
        text("SELECT migration_role FROM runtime_attention_admin.bootstrap_configuration")
    ).scalar_one()


@pytest.mark.integration
def test_bounds_do_not_outlive_admission(fresh_db, postgres_container) -> None:
    engine = create_engine(_admin_url(postgres_container, fresh_db("core")))
    try:
        with engine.connect() as connection:
            defaults = {
                name: connection.execute(text(f"SHOW {name}")).scalar_one() for name in _BOUNDS
            }
            assert set(defaults.values()) != {"1"}
            check_bootstrap_connection(connection)
            # Same connection and same transaction: the caller's next statement.
            assert {
                name: connection.execute(text(f"SHOW {name}")).scalar_one() for name in _BOUNDS
            } == defaults
            connection.rollback()
            assert {
                name: connection.execute(text(f"SHOW {name}")).scalar_one() for name in _BOUNDS
            } == defaults
    finally:
        engine.dispose()


@pytest.mark.integration
def test_autocommit_connection_is_admitted_unbounded(fresh_db, postgres_container) -> None:
    """No transaction scopes ``SET LOCAL`` there, so admission runs as it did before."""
    engine = create_engine(
        _admin_url(postgres_container, fresh_db("core")), isolation_level="AUTOCOMMIT"
    )
    try:
        with engine.connect() as connection:
            defaults = {
                name: connection.execute(text(f"SHOW {name}")).scalar_one() for name in _BOUNDS
            }
            check_bootstrap_connection(connection)
            assert {
                name: connection.execute(text(f"SHOW {name}")).scalar_one() for name in _BOUNDS
            } == defaults
    finally:
        engine.dispose()


# Each poison breaks one admitted property, uncommitted, and admission must refuse it.
# ``{role}`` is the database's normal migration login. The first eight mirror the
# bootstrap lifecycle negatives; the rest reach the finalized-interface predicates
# whose plans the bound changes.
_POISONS = (
    "ALTER ROLE {role} CREATEDB",
    "ALTER SCHEMA restore_drill_executor_admin OWNER TO {role}",
    "ALTER FUNCTION runtime_attention_admin.finalize_interface() RENAME TO forged_finalizer",
    "ALTER FUNCTION restore_drill_executor_admin.install_interface() SECURITY INVOKER",
    "ALTER FUNCTION dnd_generation_admin.finalize_interface() SET search_path TO public",
    "REVOKE butler_general_rw FROM {role}",
    "ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA general "
    "REVOKE INSERT ON TABLES FROM butler_general_rw",
    "ALTER TABLE runtime_attention_admin.bootstrap_configuration OWNER TO {role}",
    "ALTER TABLE public.runtime_attention_outbox OWNER TO {role}",
    "ALTER TABLE public.runtime_attention_outbox NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE public.dnd_generation_guard OWNER TO {role}",
    "GRANT INSERT ON public.dnd_generation_guard TO PUBLIC",
)


@pytest.mark.integration
def test_every_refusal_case_still_refuses(fresh_db, postgres_container) -> None:
    engine = create_engine(_admin_url(postgres_container, fresh_db("core")))
    try:
        with engine.connect() as connection:
            role = _migration_role(connection)
            connection.rollback()  # end the read's autobegun transaction
            transaction = connection.begin()
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            check_bootstrap_connection(connection)  # the unpoisoned database is admitted
            transaction.rollback()
        admitted = []
        for poison in _POISONS:
            with engine.connect() as connection:
                transaction = connection.begin()
                connection.execute(text(poison.format(role=f'"{role}"')))
                connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                try:
                    check_bootstrap_connection(connection)
                except BootstrapPrerequisiteError:
                    pass
                else:
                    admitted.append(poison)
                transaction.rollback()
        assert admitted == []
    finally:
        engine.dispose()
