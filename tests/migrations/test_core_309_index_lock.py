"""core_309 builds its index behind an advisory lock that never holds a snapshot (bu-dhloi1.285).

The core chain runs once per butler schema over one shared table, and two
``CREATE INDEX CONCURRENTLY`` builds on one table wait for each other's virtual transactions: two
racing upgrades deadlocked. The build now waits for a lock polled in autocommit, so a waiting
upgrade holds no transaction a running build would have to wait for.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]

LOCK = 309_20261103
INDEX = "idx_owntracks_points_recorded_at_id"
FIND_INDEX = (
    "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
    f"WHERE c.relname = '{INDEX}'"
)


def wait_until_at_the_lock(probe, holder_pid: int, upgrade) -> None:
    """Return once the upgrade's session has asked for the build lock, however it asks.

    Before that, Alembic's own revision transaction is open: it reads ``alembic_version`` and then
    imports every migration script to plan the upgrade, which on a busy runner takes longer than
    the sampling threshold. core_309's autocommit block commits that transaction before it takes
    the lock, so it is not part of the wait (bu-dhloi1.311). The match is on the lock key, not on
    the polling call, so a blocking ``pg_advisory_lock`` would be sampled too.
    """
    deadline = time.monotonic() + 60
    while not probe.exec_driver_sql(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND pid NOT IN (pg_backend_pid(), %s) AND query LIKE %s",
        (holder_pid, f"%advisory_lock({LOCK})%"),
    ).scalar_one():
        assert not upgrade.done(), "the upgrade ended before it asked for the build lock"
        assert time.monotonic() < deadline, "the upgrade never asked for the build lock"
        time.sleep(0.05)


def test_a_waiting_upgrade_holds_no_transaction_and_builds_once_the_lock_frees(
    postgres_container,
):
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@core_308")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as holder:
            holder.exec_driver_sql(f"SELECT pg_advisory_lock({LOCK})")  # a build in flight
            holder_pid = holder.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            with ThreadPoolExecutor(max_workers=1) as pool:
                upgrade = pool.submit(command.upgrade, config, "core@core_309")
                try:
                    with engine.connect() as probe:
                        wait_until_at_the_lock(probe, holder_pid, upgrade)
                        stale = []
                        deadline = time.monotonic() + 2.0
                        while time.monotonic() < deadline:
                            stale.append(
                                probe.exec_driver_sql(
                                    "SELECT count(*) FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND pid NOT IN (pg_backend_pid(), %s) "
                                    "AND xact_start < now() - interval '0.5 seconds'",
                                    (holder_pid,),
                                ).scalar_one()
                            )
                            time.sleep(0.05)
                        assert not upgrade.done(), "the upgrade ran past a held build lock"
                        assert probe.exec_driver_sql(FIND_INDEX).first() is None
                        # While it waited it kept no snapshot a running build would wait for.
                        assert max(stale) == 0, stale
                finally:
                    # Free the build even when an assertion failed, or the pool's exit would
                    # wait on an upgrade still polling (the 300 s timeout bu-dhloi1.311 saw).
                    holder.exec_driver_sql(f"SELECT pg_advisory_unlock({LOCK})")
                upgrade.result(timeout=60)
            with engine.connect() as check:
                assert check.exec_driver_sql(FIND_INDEX).scalar_one() is True
                # The upgrade released the lock it took.
                assert check.exec_driver_sql(f"SELECT pg_try_advisory_lock({LOCK})").scalar_one()
    finally:
        engine.dispose()


def test_an_invalid_index_left_by_an_interrupted_build_is_repaired(postgres_container):
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@core_308")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        # What an interrupted CREATE INDEX CONCURRENTLY leaves: the name exists, invalid. A build
        # that times out waiting for an older open transaction does exactly that.
        with engine.connect() as older, engine.connect() as connection:
            older.exec_driver_sql("BEGIN")
            older.exec_driver_sql("LOCK TABLE connectors.owntracks_points IN ROW EXCLUSIVE MODE")
            connection.exec_driver_sql("SET statement_timeout = '700ms'")
            with pytest.raises(DBAPIError):
                connection.exec_driver_sql(
                    f"CREATE INDEX CONCURRENTLY {INDEX} "
                    "ON connectors.owntracks_points (recorded_at ASC, id ASC)"
                )
            older.exec_driver_sql("ROLLBACK")
        with engine.connect() as connection:
            assert connection.exec_driver_sql(FIND_INDEX).scalar_one() is False

        command.upgrade(config, "core@core_309")

        with engine.connect() as connection:
            assert connection.exec_driver_sql(FIND_INDEX).scalar_one() is True
            assert connection.exec_driver_sql(f"SELECT pg_try_advisory_lock({LOCK})").scalar_one()
    finally:
        engine.dispose()
