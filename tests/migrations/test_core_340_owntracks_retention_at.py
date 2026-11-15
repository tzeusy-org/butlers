"""core_340: the immutable per-row retention clock on ``connectors.owntracks_points``.

Rule (``location_retention.RAW_AGE_BASIS``, the points adapter's clock-skew clamp):
``retention_at = recorded_at`` when ``ts - recorded_at > 4 hours``, else ``ts``. Every case
reads the value back from a committed row written through a real migrated database.
"""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.chronicler.adapters.owntracks import CLOCK_SKEW_THRESHOLD_HOURS
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

_ROOT = Path(__file__).resolve().parents[2]
_CORE_340 = _ROOT / "alembic/versions/core/core_340_owntracks_points_retention_at.py"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
_RECEIVED = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)

_INSERT = """
    INSERT INTO connectors.owntracks_points
        (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
    VALUES ($1, $2, 1.3, 103.8, 'owntracks:alice', $3)
    RETURNING id
"""


def _load_core_340():
    spec = importlib.util.spec_from_file_location("core_340_under_test", _CORE_340)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_the_migration_rule_is_the_adapters_clock_skew_rule() -> None:
    module = _load_core_340()
    assert module.revision == "core_340"
    assert CLOCK_SKEW_THRESHOLD_HOURS == 4
    assert module.RETENTION_AT_SQL.format(ts="ts", recorded_at="r") == (
        "CASE WHEN ts - r > interval '4 hours' THEN r ELSE ts END"
    )


@pytest.fixture
def head_url(fresh_db) -> str:
    return fresh_db("core")


@pytest.fixture
def pre_340_url(fresh_db) -> str:
    return fresh_db("core", revisions={"core": "core_338"})


_CASES = [
    ("normal", _RECEIVED - timedelta(seconds=5), "ts"),
    ("future_skew", _RECEIVED + timedelta(hours=4, seconds=1), "recorded_at"),
    ("exactly_four_hours", _RECEIVED + timedelta(hours=4), "ts"),
    ("offline_queue", _RECEIVED - timedelta(days=40), "ts"),
]


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_insert_trigger_derives_the_clock_and_ignores_a_caller_value(
    db, head_url: str
) -> None:
    points = await db.open(head_url)
    for name, ts, basis in _CASES:
        row_id = await points.fetchval(_INSERT, f"case:{name}", ts, _RECEIVED)
        stored = await points.fetchval(
            "SELECT retention_at FROM connectors.owntracks_points WHERE id = $1", row_id
        )
        assert stored == (ts if basis == "ts" else _RECEIVED), name

    forged = await points.fetchval(
        """
        INSERT INTO connectors.owntracks_points
            (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at, retention_at)
        VALUES ('case:forged', $1, 1.3, 103.8, 'owntracks:alice', $1, $2)
        RETURNING retention_at
        """,
        _RECEIVED,
        _RECEIVED + timedelta(days=365),
    )
    assert forged == _RECEIVED


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_update_guard_refuses_moving_the_clock_but_allows_other_columns(
    db, head_url: str
) -> None:
    points = await db.open(head_url)
    row_id = await points.fetchval(_INSERT, "case:guard", _RECEIVED, _RECEIVED)
    for column, value in (
        ("retention_at", _RECEIVED + timedelta(days=90)),
        ("ts", _RECEIVED + timedelta(days=90)),
        ("recorded_at", _RECEIVED + timedelta(days=90)),
    ):
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match="immutable"):
            await points.execute(
                f"UPDATE connectors.owntracks_points SET {column} = $2 WHERE id = $1",
                row_id,
                value,
            )
    # Positive companion: the guard is narrow, not a blanket write block.
    await points.execute(
        "UPDATE connectors.owntracks_points SET accuracy = 12.5 WHERE id = $1", row_id
    )
    row = await points.fetchrow(
        "SELECT accuracy, ts, recorded_at, retention_at FROM connectors.owntracks_points "
        "WHERE id = $1",
        row_id,
    )
    assert row["accuracy"] == 12.5
    assert row["ts"] == row["recorded_at"] == row["retention_at"] == _RECEIVED


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_upgrade_backfills_pre_existing_rows_by_the_same_rule(db, pre_340_url: str) -> None:
    before = await db.open(pre_340_url)
    ids = {}
    for name, ts, basis in _CASES:
        ids[name] = (await before.fetchval(_INSERT, f"legacy:{name}", ts, _RECEIVED), ts, basis)
    assert not await before.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'connectors' AND table_name = 'owntracks_points' "
        "AND column_name = 'retention_at')"
    )
    await before.close()

    await run_migrations(pre_340_url, chain="core")

    after = await db.open(pre_340_url)
    for name, (row_id, ts, basis) in ids.items():
        stored = await after.fetchval(
            "SELECT retention_at FROM connectors.owntracks_points WHERE id = $1", row_id
        )
        assert stored == (ts if basis == "ts" else _RECEIVED), name
    assert (
        await after.fetchval(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'connectors' AND table_name = 'owntracks_points' "
            "AND column_name = 'retention_at'"
        )
        == "NO"
    )


# Catalog rows a repeated install would rewrite: trigger oids, function oid and xmin, and the
# temporary NOT NULL check (which must be gone).
_SHARED_CATALOG = """
SELECT
    (SELECT array_agg(tgname || ':' || oid ORDER BY tgname) FROM pg_trigger
     WHERE tgrelid = 'connectors.owntracks_points'::regclass AND NOT tgisinternal),
    (SELECT array_agg(proname || ':' || oid || ':' || xmin ORDER BY proname) FROM pg_proc
     WHERE pronamespace = 'connectors'::regnamespace AND proname LIKE 'owntracks_points_%'),
    (SELECT count(*) FROM pg_constraint
     WHERE conrelid = 'connectors.owntracks_points'::regclass
       AND conname = 'owntracks_points_retention_at_not_null')
"""


def _shared_catalog(db_url: str):
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            return tuple(connection.execute(text(_SHARED_CATALOG)).one())
    finally:
        engine.dispose()


@_docker
@pytest.mark.integration
def test_first_run_backfills_in_pages_and_later_schema_runs_write_nothing(
    postgres_container,
) -> None:
    """The shared table is installed once; every other schema's core run is a catalog no-op.

    12 001 legacy rows span three backfill pages, so the keyset loop must advance past a page.
    """
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    command.upgrade(first, "core_338")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO connectors.owntracks_points
                        (idempotency_key, ts, lat, lon, endpoint_identity, recorded_at)
                    SELECT 'legacy:' || n,
                           CASE WHEN mod(n, 2) = 0
                                THEN CAST(:received AS timestamptz) + interval '5 hours'
                                ELSE CAST(:received AS timestamptz) - interval '1 day' END,
                           1.3, 103.8, 'owntracks:alice', CAST(:received AS timestamptz)
                    FROM generate_series(1, 12001) AS n
                    """
                ),
                {"received": _RECEIVED},
            )
    finally:
        engine.dispose()

    command.upgrade(first, "core@head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            counts = connection.execute(
                text(
                    """
                    SELECT count(*) FILTER (WHERE retention_at IS NULL),
                           count(*) FILTER (WHERE retention_at = recorded_at),
                           count(*) FILTER (WHERE retention_at = ts AND ts < recorded_at)
                    FROM connectors.owntracks_points
                    """
                )
            ).one()
    finally:
        engine.dispose()
    assert tuple(counts) == (0, 6000, 6001)

    installed = _shared_catalog(db_url)
    assert len(installed[0]) == 2 and len(installed[1]) == 2 and installed[2] == 0

    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(second, "core@head")
    assert _shared_catalog(db_url) == installed
