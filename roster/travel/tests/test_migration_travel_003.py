"""travel_003 executes: PNR-keyed booking identity, traveller party, connections.

Runs ``upgrade()`` for real against a travel_002-shaped schema pre-seeded with
a leg referencing 'ORD' (proving the airport_minimum_connect dynamic backfill),
then asserts every new table/column, the curated + backfilled minimum-connect
seed, idempotence, and downgrade.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "003_journey_identity.py"

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
_session_loop = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_travel_003", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.unit
def test_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "travel_003"
    assert mod.down_revision == "travel_002"


class _CollectingOp:
    """Minimal ``alembic.op`` stand-in that records statements in order."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


def _upgrade_statements() -> list[str]:
    mod = _load_migration()
    fake = _CollectingOp()
    mod.op = fake
    mod.upgrade()
    return fake.statements


def _downgrade_statements() -> list[str]:
    mod = _load_migration()
    fake = _CollectingOp()
    mod.op = fake
    mod.downgrade()
    return fake.statements


_PRE_MIGRATION_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS travel;

CREATE TABLE IF NOT EXISTS travel.trips (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    destination TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    status      TEXT NOT NULL DEFAULT 'planned',
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS travel.legs (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trip_id                   UUID NOT NULL REFERENCES travel.trips(id) ON DELETE CASCADE,
    type                      TEXT NOT NULL DEFAULT 'flight',
    carrier                   TEXT,
    departure_airport_station TEXT,
    departure_city            TEXT,
    departure_at              TIMESTAMPTZ NOT NULL,
    arrival_airport_station   TEXT,
    arrival_city              TEXT,
    arrival_at                TIMESTAMPTZ NOT NULL,
    confirmation_number       TEXT,
    pnr                       TEXT,
    seat                      TEXT,
    metadata                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@pytest.fixture
async def pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as p:
        await p.execute(_PRE_MIGRATION_SCHEMA)
        trip_id = await p.fetchval(
            """
            INSERT INTO travel.trips (name, destination, start_date, end_date, status)
            VALUES ('Trip', 'Tokyo', '2026-06-01', '2026-06-10', 'planned')
            RETURNING id
            """
        )
        # A leg referencing SFO/ORD (neither in the curated hub list) proves
        # the dynamic airport_minimum_connect backfill.
        await p.execute(
            """
            INSERT INTO travel.legs (trip_id, type, departure_airport_station,
                                      arrival_airport_station, departure_at, arrival_at)
            VALUES ($1::uuid, 'flight', 'SFO', 'ORD',
                    '2026-06-01T10:00:00+00:00', '2026-06-01T20:00:00+00:00')
            """,
            trip_id,
        )
        yield p, trip_id


@_docker
@_session_loop
class TestMigrationRunsAgainstPostgres:
    async def test_upgrade_creates_every_new_table(self, pool) -> None:
        p, _trip_id = pool
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)

        for table in (
            "booking_records",
            "travellers",
            "leg_passengers",
            "airport_minimum_connect",
            "connections",
        ):
            exists = await p.fetchval("SELECT to_regclass($1) IS NOT NULL", f"travel.{table}")
            assert exists is True, f"travel.{table} was not created"

        for column in ("segment_index", "booking_record_id"):
            has_column = await p.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'travel' AND table_name = 'legs'
                      AND column_name = $1
                )
                """,
                column,
            )
            assert has_column is True, f"travel.legs.{column} was not added"

    async def test_upgrade_seeds_curated_and_backfilled_minimum_connect(self, pool) -> None:
        p, _trip_id = pool
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)

        pek = await p.fetchrow(
            "SELECT minimum_connect_minutes, source FROM travel.airport_minimum_connect "
            "WHERE airport_code = 'PEK'"
        )
        assert pek["minimum_connect_minutes"] == 90
        assert pek["source"] == "curated"

        # Dynamic backfill: SFO and ORD were referenced by the pre-existing leg.
        for airport in ("SFO", "ORD"):
            row = await p.fetchrow(
                "SELECT minimum_connect_minutes, source FROM travel.airport_minimum_connect "
                "WHERE airport_code = $1",
                airport,
            )
            assert row is not None, f"{airport} was not backfilled"
            assert row["source"] == "backfill"
            assert row["minimum_connect_minutes"] == 60

    async def test_upgrade_is_idempotent(self, pool) -> None:
        p, _trip_id = pool
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)
            for statement in _upgrade_statements():
                await conn.execute(statement)

        count = await p.fetchval(
            "SELECT count(*) FROM travel.airport_minimum_connect WHERE airport_code = 'PEK'"
        )
        assert count == 1

    async def test_downgrade_drops_new_tables_and_columns(self, pool) -> None:
        p, _trip_id = pool
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)
            for statement in _downgrade_statements():
                await conn.execute(statement)

        for table in (
            "booking_records",
            "travellers",
            "leg_passengers",
            "airport_minimum_connect",
            "connections",
        ):
            exists = await p.fetchval("SELECT to_regclass($1) IS NOT NULL", f"travel.{table}")
            assert exists is False, f"travel.{table} survived downgrade"

        for column in ("segment_index", "booking_record_id"):
            has_column = await p.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'travel' AND table_name = 'legs'
                      AND column_name = $1
                )
                """,
                column,
            )
            assert has_column is False, f"travel.legs.{column} survived downgrade"
