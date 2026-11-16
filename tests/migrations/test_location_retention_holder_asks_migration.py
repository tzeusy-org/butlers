"""chronicler_040: when Chronicler last asked a holder about a retention grant.

Applies the real upgrade at chronicler_039 and checks, against Postgres: the runtime role may
read, insert and update (the row is scheduling state, rewritten on every ask) but not delete or
truncate, one row per grant and holder naming an existing grant, a re-run only reads the
catalog, and the downgrade drops only this table.

Issue: bu-s11n0s.7 (slice S4c-3)
"""

from __future__ import annotations

import shutil
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations import test_location_retention_holder_receipts_migration as _receipts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ROLE = "butler_chronicler_rw"
_TABLE = "chronicler.location_retention_holder_asks"
_HOLDER = "switchboard.message_inbox"
_ASK = (
    f"INSERT INTO {_TABLE} (grant_id, holder, asked_at) VALUES (:grant, :holder, now()) "
    "ON CONFLICT (grant_id, holder) DO UPDATE SET asked_at = EXCLUDED.asked_at"
)
_CATALOG = f"""
SELECT
    (SELECT relname || ':' || xmin::text FROM pg_class WHERE oid = '{_TABLE}'::regclass),
    (SELECT array_agg(conname || ':' || xmin::text ORDER BY conname) FROM pg_constraint
     WHERE conrelid = '{_TABLE}'::regclass)
"""
_config = _receipts._config
_grant = _receipts._grant


@pytest.fixture
def chronicler_040_db(postgres_container):
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_040")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def test_the_runtime_role_may_read_insert_and_update(chronicler_040_db) -> None:
    _, engine = chronicler_040_db
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


def test_one_row_per_grant_and_holder_for_an_existing_grant(chronicler_040_db) -> None:
    _, engine = chronicler_040_db
    with engine.begin() as conn:
        grant = _grant(conn)
        conn.execute(text(_ASK), {"grant": grant, "holder": _HOLDER})
        conn.execute(text(_ASK), {"grant": grant, "holder": _HOLDER})
    for grant_id, holder in ((uuid4(), _HOLDER), (grant, "Switchboard Inbox")):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(_ASK), {"grant": grant_id, "holder": holder})
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 1


def test_a_rerun_only_reads_the_catalog_and_the_downgrade_drops_it(chronicler_040_db) -> None:
    db_url, engine = chronicler_040_db
    with engine.begin() as conn:
        conn.execute(text(_ASK), {"grant": _grant(conn), "holder": _HOLDER})
    with engine.connect() as conn:
        installed = tuple(conn.execute(text(_CATALOG)).one())
    command.stamp(_config(db_url), "chronicler_039")
    command.upgrade(_config(db_url), "chronicler@chronicler_040")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed, "re-run wrote the catalog"

    command.downgrade(_config(db_url), "chronicler_039")
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT to_regclass('{_TABLE}')")).scalar_one() is None
        assert (
            conn.execute(
                text("SELECT count(*) FROM chronicler.location_retention_grants")
            ).scalar_one()
            == 1
        )
