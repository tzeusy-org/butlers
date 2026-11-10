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
                with engine.connect() as probe:
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
                holder.exec_driver_sql(f"SELECT pg_advisory_unlock({LOCK})")
                upgrade.result(timeout=60)
            with engine.connect() as check:
                assert check.exec_driver_sql(FIND_INDEX).scalar_one() is True
                # The upgrade released the lock it took.
                assert check.exec_driver_sql(f"SELECT pg_try_advisory_lock({LOCK})").scalar_one()
    finally:
        engine.dispose()
