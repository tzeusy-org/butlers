"""travel_002 executes: the raw_pre_migration backfill for double-encoded metadata.

``roster/travel/tests/test_bookings.py`` proves the *write-path* fix (new writes
no longer double-encode). It says nothing about existing rows written by the
pre-fix code, which is what this migration repairs. This runs ``upgrade()`` for
real against a schema seeded with pre-fix double-encoded metadata (mirroring
what ``json.dumps()`` bound to a codec-registered ``$n::jsonb`` parameter
actually produced), then asserts the backfill and its idempotence/rollback.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "002_metadata_backfill.py"

_docker = pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
_session_loop = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.unit
def test_revision_chain() -> None:
    mod = load_migration(_MIGRATION_PATH)
    assert mod.revision == "travel_002"
    assert mod.down_revision == "travel_001"


class _CollectingOp:
    """Minimal ``alembic.op`` stand-in that records statements in order."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


def _upgrade_statements() -> list[str]:
    mod = load_migration(_MIGRATION_PATH)
    fake = _CollectingOp()
    mod.op = fake
    mod.upgrade()
    return fake.statements


def _downgrade_statements() -> list[str]:
    mod = load_migration(_MIGRATION_PATH)
    fake = _CollectingOp()
    mod.op = fake
    mod.downgrade()
    return fake.statements


_TABLES = ("trips", "legs", "accommodations", "reservations", "documents")


@pytest.fixture
async def pre_migration(db):
    """The real travel schema at travel_001, seeded with pre-fix double-encoded metadata.

    Bound as a raw string (bypassing the jsonb codec's own encode step) so the
    stored value is exactly what the pre-fix ``json.dumps()`` call site
    produced: a jsonb scalar holding the JSON text as its content, i.e.
    ``jsonb_typeof = 'string'``.
    """
    p = await db("core", "travel", schemas={"travel": "travel"}, revisions={"travel": "travel_001"})

    # `to_jsonb($n::text)` wraps a plain TEXT parameter as a jsonb *string*
    # scalar (bypassing the connection's jsonb codec, which only
    # intercepts the jsonb type OID) -- exactly what the pre-fix
    # `json.dumps()` -> codec-encode double-serialization produced.
    trip_id = await p.fetchval(
        """
        INSERT INTO travel.trips (name, destination, start_date, end_date, status, metadata)
        VALUES ('Trip', 'Tokyo', '2026-06-01', '2026-06-10', 'planned', to_jsonb($1::text))
        RETURNING id
        """,
        json.dumps({"source_message_id": "m1"}),
    )
    for table, kind, extra_cols, extra_vals in (
        (
            "legs",
            "flight",
            ", departure_at, arrival_at",
            ", '2026-06-01T10:00:00+00:00', '2026-06-01T20:00:00+00:00'",
        ),
        ("accommodations", "hotel", "", ""),
        ("reservations", "activity", "", ""),
        ("documents", "receipt", "", ""),
    ):
        await p.execute(
            f"""
            INSERT INTO travel.{table} (trip_id, type, metadata{extra_cols})
            VALUES ($1::uuid, $3, to_jsonb($2::text){extra_vals})
            """,
            trip_id,
            json.dumps({"flight_number": "DD94XR"} if table == "legs" else {"note": "x"}),
            kind,
        )
    return p, trip_id


@_docker
@_session_loop
class TestMigrationRunsAgainstPostgres:
    async def test_pre_migration_fixture_is_actually_double_encoded(self, pre_migration) -> None:
        """Sanity check on the fixture itself: it reproduces the live bug shape."""
        p, _ = pre_migration
        for table in _TABLES:
            typeof = await p.fetchval(f"SELECT jsonb_typeof(metadata) FROM travel.{table} LIMIT 1")
            assert typeof == "string", f"fixture for {table} was not double-encoded"

    async def test_upgrade_backfills_every_table_to_real_objects(self, pre_migration) -> None:
        p, trip_id = pre_migration
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)

        for table in _TABLES:
            typeof = await p.fetchval(f"SELECT jsonb_typeof(metadata) FROM travel.{table} LIMIT 1")
            assert typeof == "object"

        leg_row = await p.fetchrow(
            "SELECT metadata, raw_pre_migration FROM travel.legs WHERE trip_id = $1::uuid", trip_id
        )
        assert leg_row["metadata"]["flight_number"] == "DD94XR"
        # raw_pre_migration round-trips the exact original (double-encoded)
        # value for exact rollback.
        assert json.loads(leg_row["raw_pre_migration"]) == {"flight_number": "DD94XR"}

    async def test_upgrade_is_idempotent(self, pre_migration) -> None:
        p, trip_id = pre_migration
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)
            # Re-running is a no-op: the WHERE guard no longer matches once
            # metadata is a real object.
            for statement in _upgrade_statements():
                await conn.execute(statement)

        leg_row = await p.fetchrow(
            "SELECT metadata FROM travel.legs WHERE trip_id = $1::uuid", trip_id
        )
        assert leg_row["metadata"]["flight_number"] == "DD94XR"

    async def test_downgrade_restores_the_original_string_and_drops_the_column(
        self, pre_migration
    ) -> None:
        p, trip_id = pre_migration
        async with p.acquire() as conn:
            for statement in _upgrade_statements():
                await conn.execute(statement)
            for statement in _downgrade_statements():
                await conn.execute(statement)

        typeof = await p.fetchval(
            "SELECT jsonb_typeof(metadata) FROM travel.legs WHERE trip_id = $1::uuid", trip_id
        )
        assert typeof == "string"

        has_column = await p.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'travel' AND table_name = 'legs'
                  AND column_name = 'raw_pre_migration'
            )
            """
        )
        assert has_column is False
