"""chronicler_038: the location retention job's single run record.

Applies the real upgrade at chronicler_037 and checks, against Postgres: the runtime role may
read, insert and update the row but not delete it, there is only ever one row with a known
outcome and a non-negative streak, a re-run only reads the catalog, and the downgrade drops the
table but keeps chronicler_037's.

Issue: bu-s11n0s.7 (slice S4b-4c)
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ROLE = "butler_chronicler_rw"
_TABLE = "chronicler.location_retention_runs"
_INSERT = (
    f"INSERT INTO {_TABLE} (id, last_started_at, last_outcome, consecutive_failures) "
    "VALUES (:id, now(), :outcome, :streak)"
)
_CATALOG = f"""
SELECT
    (SELECT relname || ':' || xmin::text FROM pg_class WHERE oid = '{_TABLE}'::regclass),
    (SELECT array_agg(conname || ':' || xmin::text ORDER BY conname) FROM pg_constraint
     WHERE conrelid = '{_TABLE}'::regclass)
"""


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


@pytest.fixture
def chronicler_038_db(postgres_container):
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_038")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def test_the_runtime_role_may_read_insert_and_update_only(chronicler_038_db) -> None:
    _, engine = chronicler_038_db
    with engine.connect() as conn:
        granted = {
            privilege: conn.execute(
                text("SELECT has_table_privilege(:role, :table, :privilege)"),
                {"role": _ROLE, "table": _TABLE, "privilege": privilege},
            ).scalar_one()
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }
    assert granted == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": True,
        "DELETE": False,
        "TRUNCATE": False,
    }


def test_one_row_with_a_known_outcome(chronicler_038_db) -> None:
    _, engine = chronicler_038_db
    with engine.begin() as conn:
        conn.execute(text(_INSERT), {"id": 1, "outcome": "running", "streak": 0})

    for row in (
        {"id": 2, "outcome": "running", "streak": 0},
        {"id": 1, "outcome": "succeeded", "streak": 0},
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(_INSERT), row)
    for assignment in ("last_outcome = 'paused'", "consecutive_failures = -1"):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE {_TABLE} SET {assignment}"))


def test_a_rerun_only_reads_the_catalog_and_the_downgrade_drops_the_table(
    chronicler_038_db,
) -> None:
    db_url, engine = chronicler_038_db
    with engine.connect() as conn:
        installed = tuple(conn.execute(text(_CATALOG)).one())
    command.stamp(_config(db_url), "chronicler_037")
    command.upgrade(_config(db_url), "chronicler@chronicler_038")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed, "re-run wrote the catalog"

    command.downgrade(_config(db_url), "chronicler_037")
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT to_regclass('{_TABLE}')")).scalar_one() is None
        assert (
            conn.execute(
                text("SELECT to_regclass('chronicler.location_retention_grant_states')")
            ).scalar_one()
            is not None
        )
