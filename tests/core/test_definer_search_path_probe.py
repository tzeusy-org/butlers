"""Real-PostgreSQL proof for the definer search-path probe (bu-hefzis).

The probe must report clean on a fresh bootstrap, name a regressed definer with
its owner and remedy, converge after the documented remedy, and judge only the
``search_path`` entry.
"""

from __future__ import annotations

import asyncio
import shutil
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.core.definer_search_path import (
    REMEDY_INIT_DB,
    REMEDY_MIGRATIONS,
    compute_unpinned_definers,
)
from butlers.migrations import run_migrations
from butlers.testing.migration import (
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_IS_DUE = "restore_drill_executor.is_due(p_interval_seconds integer)"


@pytest.fixture
def bootstrapped(postgres_container) -> tuple[str, str]:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    return db_url, migration_bootstrap_db_url(postgres_container, db_name)


def _execute(url: str, statement: str) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


def _rerun_init_db(bootstrap_url: str, migration_url: str) -> None:
    """Run the checked-in bootstrap again, exactly as the runbook remedy does."""
    engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    raw_connection = engine.raw_connection()
    try:
        raw_connection.autocommit = True
        with raw_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)",
                (unquote(urlparse(migration_url).username or ""),),
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw_connection.close()
        engine.dispose()


async def _probe(db_url: str):
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=1)
    try:
        return await compute_unpinned_definers(pool)
    finally:
        await pool.close()


async def test_probe_is_clean_then_names_a_fenced_regression_until_init_db_rerun(
    bootstrapped,
) -> None:
    db_url, bootstrap_url = bootstrapped

    fresh = await _probe(db_url)
    assert fresh.is_available
    assert fresh.checked_count > 40
    assert fresh.entries == ()

    # The shape of a database bootstrapped before the pins.
    _execute(
        bootstrap_url,
        "ALTER FUNCTION restore_drill_executor.is_due(integer) "
        "SET search_path = pg_catalog, public, pg_temp",
    )
    regressed = await _probe(db_url)
    assert [
        (entry.signature, entry.owner, entry.search_path, entry.remedy)
        for entry in regressed.entries
    ] == [(_IS_DUE, "restore_drill_executor_owner", "pg_catalog, public, pg_temp", REMEDY_INIT_DB)]

    _rerun_init_db(bootstrap_url, db_url)
    converged = await _probe(db_url)
    assert converged.is_available
    assert converged.entries == ()


async def test_probe_judges_only_the_search_path_entry_and_degrades_on_read_failure(
    bootstrapped,
) -> None:
    db_url, _bootstrap_url = bootstrapped
    migration_login = unquote(urlparse(db_url).username or "")
    # A missing pg_temp is unpinned: implicit pg_temp is searched first.
    _execute(
        db_url,
        "ALTER FUNCTION public.dashboard_turn_require_role(text) SET search_path = pg_catalog",
    )
    # An invoker is out of scope whatever its path.
    _execute(
        db_url,
        "CREATE FUNCTION public.probe_invoker() RETURNS int LANGUAGE sql "
        "SET search_path = pg_catalog, public, pg_temp AS 'SELECT 1'",
    )

    report = await _probe(db_url)
    assert report.is_available
    assert [
        (entry.signature, entry.owner, entry.search_path, entry.remedy) for entry in report.entries
    ] == [
        (
            "public.dashboard_turn_require_role(p_expected_role text)",
            migration_login,
            "pg_catalog",
            REMEDY_MIGRATIONS,
        )
    ]
    # cost_claim_restore_row is pinned and also carries row_security=on.
    assert not any("cost_claim_restore_row" in entry.signature for entry in report.entries)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=1)
    await pool.close()
    degraded = await compute_unpinned_definers(pool)
    assert not degraded.is_available
    assert degraded.entries == ()
    assert degraded.check_error is not None
