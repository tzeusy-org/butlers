"""The shared DB factory: ``db``, ``db_pool`` and ``fresh_db`` (bu-rvgzmf.1)."""

from __future__ import annotations

import time
import warnings

import pytest

from butlers.testing.migration import table_exists

pytestmark = pytest.mark.integration

# Async fixtures run on the session loop (asyncio_default_fixture_loop_scope), so tests
# that use their pools must too.
session_loop = pytest.mark.asyncio(loop_scope="session")


@session_loop
async def test_db_pool_is_a_private_core_migrated_database(db_pool, db) -> None:
    other = await db("core")
    for pool in (db_pool, other):
        assert await pool.fetchval("SELECT to_regclass('public.state') IS NOT NULL")
    mine = await db_pool.fetchval("SELECT current_database()")
    assert mine != await other.fetchval("SELECT current_database()")
    await db_pool.execute("INSERT INTO state (key, value) VALUES ('k', '{}'::jsonb)")
    assert await other.fetchval("SELECT count(*) FROM state WHERE key = 'k'") == 0


@session_loop
async def test_db_scopes_a_butler_schema_and_round_trips_jsonb(db) -> None:
    pool = await db(
        "core", "switchboard", schemas={"switchboard": "switchboard"}, schema="switchboard"
    )
    assert (await pool.fetchval("SHOW search_path")).startswith("switchboard")
    assert await pool.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname = 'switchboard'")
    assert await pool.fetchval("SELECT '{\"a\": 1}'::jsonb") == {"a": 1}


@session_loop
async def test_db_open_pools_a_fresh_db_url_it_will_close(db, fresh_db) -> None:
    url = fresh_db("core", "switchboard", schemas={"switchboard": "switchboard"})
    pool = await db.open(url, schema="switchboard")
    assert (await pool.fetchval("SHOW search_path")).startswith("switchboard")
    assert await pool.fetchval("SELECT '{\"a\": 1}'::jsonb") == {"a": 1}
    assert table_exists(url, "state")


def test_fresh_db_stops_at_the_requested_revision(fresh_db) -> None:
    head = fresh_db("core")
    early = fresh_db("core", revisions={"core": "core_001"})
    assert head != early
    # core_001 creates state; core_002 adds public.entities.
    assert table_exists(head, "state") and table_exists(early, "state")
    assert table_exists(head, "entities") and not table_exists(early, "entities")


def test_clone_cost_is_reported(fresh_db) -> None:
    fresh_db("core")  # the template exists before timing clones
    started = time.monotonic()
    for _ in range(5):
        fresh_db("core")
    per_clone = (time.monotonic() - started) / 5
    # junit properties are lost under xdist and passing output is captured; a warning
    # reaches the CI log's warnings summary.
    warnings.warn(f"CORE-CLONE-SECONDS {per_clone:.3f}", UserWarning, stacklevel=1)
    assert per_clone < 30


@session_loop
async def test_fresh_provision_metadata_does_not_transfer_pool_ownership(
    provisioned_postgres_pool, postgres_container, monkeypatch
) -> None:
    from butlers.testing import storage

    events = []
    original = storage.database_event

    def record(event, container, name, kind):
        events.append((event, container, name, kind))
        original(event, container, name, kind)

    monkeypatch.setattr(storage, "database_event", record)
    async with provisioned_postgres_pool() as pool:
        name = await pool.fetchval("SELECT current_database()")
        assert [
            (event, kind)
            for event, container, dbname, kind in events
            if container is postgres_container and dbname == name
        ] == [("intent", "F"), ("created", "F"), ("ready", "F")]
        assert await pool.fetchval("SELECT 1") == 1
    # Closing a borrower pool is not deletion authority for a physical database.
    from sqlalchemy import create_engine, text

    engine = create_engine(postgres_container.get_connection_url())
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=:name)"),
                {"name": name},
            ).scalar_one()
    finally:
        engine.dispose()
