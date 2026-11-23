"""chronicler_035: forgotten-row receipts and location evidence tombstones.

Applies the real upgrade at chronicler_034 and checks, against Postgres: the runtime role may
only read and append, both tables refuse UPDATE and DELETE, a tombstone names an OwnTracks
source once and never without its receipt, the writer records a re-run without doubling its
count, the projection's resurrection floor sees a tombstone, a re-run of the revision only
reads the catalog, and the downgrade drops both tables and the guard.

Issue: bu-s11n0s.7 (slice 3)
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from butlers.chronicler.location_forgetting import (
    ForgottenPointEvent,
    owntracks_point_forgotten,
    record_forgetting,
)
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ROLE = "butler_chronicler_rw"
_TABLES = ("location_forgetting_receipts", "location_evidence_tombstones")
_CUTOFF = datetime(2026, 10, 16, tzinfo=UTC)
_REF = "connectors.owntracks_points:owntracks:alice:1760000000:location"

_INSERT_RECEIPT = (
    "INSERT INTO chronicler.location_forgetting_receipts (id, policy_version, cutoff, "
    "spatial_scheme, spatial_precision_m, point_events_forgotten, episodes_coarsened) "
    "VALUES (:id, 1, :cutoff, 'eqcell-150m-v1', 150, 1, 0)"
)
_INSERT_TOMBSTONE = (
    "INSERT INTO chronicler.location_evidence_tombstones (point_event_id, source_ref, receipt_id) "
    "VALUES (:event, :ref, :receipt)"
)
_COUNTS = (
    "SELECT (SELECT count(*) FROM chronicler.location_forgetting_receipts), "
    "(SELECT count(*) FROM chronicler.location_evidence_tombstones)"
)
# Every catalog row the revision writes: tables (including their ACLs), constraints, triggers
# and the guard function. A catalog-read-only re-run leaves each row's xmin unchanged.
_CATALOG = """
SELECT
    (SELECT array_agg(relname || ':' || xmin::text ORDER BY relname) FROM pg_class
     WHERE relnamespace = 'chronicler'::regnamespace
       AND relname LIKE 'location_%' AND relname IN (
           'location_forgetting_receipts', 'location_evidence_tombstones',
           'location_evidence_tombstones_receipt_idx')),
    (SELECT array_agg(conname || ':' || xmin::text ORDER BY conname) FROM pg_constraint
     WHERE conrelid IN ('chronicler.location_forgetting_receipts'::regclass,
                        'chronicler.location_evidence_tombstones'::regclass)),
    (SELECT array_agg(tgname || ':' || xmin::text ORDER BY tgname) FROM pg_trigger
     WHERE tgrelid IN ('chronicler.location_forgetting_receipts'::regclass,
                       'chronicler.location_evidence_tombstones'::regclass)),
    (SELECT xmin::text FROM pg_proc
     WHERE oid = 'chronicler.location_forgetting_append_only()'::regprocedure)
"""


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


def _asyncpg_url(db_url: str) -> str:
    return db_url.replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
def chronicler_035_db(postgres_container):
    # chronicler_030 refuses to run until the private memory storage exists, so stop before
    # it, prepare memory, then continue.
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_035")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def test_the_runtime_role_may_only_read_and_append(chronicler_035_db) -> None:
    _, engine = chronicler_035_db
    with engine.connect() as conn:
        for table in _TABLES:
            granted = {
                privilege: conn.execute(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {"role": _ROLE, "table": f"chronicler.{table}", "privilege": privilege},
                ).scalar_one()
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
            }
            assert granted == {
                "SELECT": True,
                "INSERT": True,
                "UPDATE": False,
                "DELETE": False,
                "TRUNCATE": False,
            }, table


def test_both_tables_are_append_only_and_a_source_is_tombstoned_once(chronicler_035_db) -> None:
    _, engine = chronicler_035_db
    receipt = uuid4()
    with engine.begin() as conn:
        conn.execute(text(_INSERT_RECEIPT), {"id": receipt, "cutoff": _CUTOFF})
        conn.execute(text(_INSERT_TOMBSTONE), {"event": uuid4(), "ref": _REF, "receipt": receipt})

    for statement in (
        "UPDATE chronicler.location_forgetting_receipts SET episodes_coarsened = 9",
        "DELETE FROM chronicler.location_forgetting_receipts",
        "UPDATE chronicler.location_evidence_tombstones SET episode_ids = '{}'",
        "DELETE FROM chronicler.location_evidence_tombstones",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))

    # The same source again, a foreign source, or a tombstone with no receipt is refused.
    for ref, receipt_id in ((_REF, receipt), ("point_events:abc", receipt), (_REF + "2", uuid4())):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(_INSERT_TOMBSTONE), {"event": uuid4(), "ref": ref, "receipt": receipt_id}
                )


def test_the_writer_and_floor_under_the_runtime_role(chronicler_035_db) -> None:
    db_url, _ = chronicler_035_db

    async def scenario() -> tuple[int, int, bool, bool]:
        conn = await asyncpg.connect(_asyncpg_url(db_url))
        try:
            await conn.execute(f"SET ROLE {_ROLE}")
            await conn.execute("SET search_path TO chronicler, public")
            forgotten = [ForgottenPointEvent(uuid4(), _REF, episode_ids=(uuid4(),))]
            first = await record_forgetting(
                conn, policy_version=1, cutoff=_CUTOFF, episodes_coarsened=1, forgotten=forgotten
            )
            again = await record_forgetting(
                conn, policy_version=1, cutoff=_CUTOFF, episodes_coarsened=0, forgotten=forgotten
            )
            stored = await conn.fetchval(
                "SELECT point_events_forgotten FROM location_forgetting_receipts WHERE id = $1",
                again.id,
            )
            assert stored == again.point_events_forgotten
            return (
                first.point_events_forgotten,
                again.point_events_forgotten,
                await owntracks_point_forgotten(conn, _REF),
                await owntracks_point_forgotten(conn, _REF + "-fresh"),
            )
        finally:
            await conn.close()

    assert asyncio.run(scenario()) == (1, 0, True, False)


def test_a_rerun_only_reads_the_catalog_and_the_downgrade_drops_everything(
    chronicler_035_db,
) -> None:
    db_url, engine = chronicler_035_db
    receipt = uuid4()
    with engine.begin() as conn:
        conn.execute(text(_INSERT_RECEIPT), {"id": receipt, "cutoff": _CUTOFF})
        conn.execute(text(_INSERT_TOMBSTONE), {"event": uuid4(), "ref": _REF, "receipt": receipt})
    with engine.connect() as conn:
        installed = tuple(conn.execute(text(_CATALOG)).one())
    command.stamp(_config(db_url), "chronicler_034")
    command.upgrade(_config(db_url), "chronicler@chronicler_035")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed, "re-run wrote the catalog"
        assert conn.execute(text(_COUNTS)).one() == (1, 1)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM chronicler.location_evidence_tombstones"))

    command.downgrade(_config(db_url), "chronicler_034")
    with engine.connect() as conn:
        for table in _TABLES:
            assert (
                conn.execute(text(f"SELECT to_regclass('chronicler.{table}')")).scalar_one() is None
            )
        assert (
            conn.execute(
                text("SELECT to_regprocedure('chronicler.location_forgetting_append_only()')")
            ).scalar_one()
            is None
        )
    # And the revision installs cleanly again.
    command.upgrade(_config(db_url), "chronicler@chronicler_035")
    with engine.connect() as conn:
        assert conn.execute(text(_COUNTS)).one() == (0, 0)
