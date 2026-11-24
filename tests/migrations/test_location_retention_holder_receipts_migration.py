"""chronicler_039: holder receipts for retention grants, appended rather than updated.

Applies the real upgrade at chronicler_038 and checks, against Postgres: the runtime role may
only read and append, the table refuses UPDATE and DELETE, a receipt names an existing grant, a
holder completes a grant at most once (``blocked`` may repeat), receipts are JSON objects, a
re-run only reads the catalog, and the downgrade refuses while a receipt is recorded and
otherwise drops only this table.

Issue: bu-s11n0s.7 (slice S4c-1)
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ROLE = "butler_chronicler_rw"
_TABLE = "chronicler.location_retention_holder_receipts"
_HOLDER = "switchboard.message_inbox"
_DIGEST = "v1:" + "a" * 64
_REVISIONS = json.dumps(
    dict.fromkeys(
        ("owntracks.points", "owntracks.place_cluster", "owntracks.ssid_presence"), _DIGEST
    )
)
_INSERT_GRANT = (
    "INSERT INTO chronicler.location_retention_grants (id, policy_version, retention_days, "
    "cutoff, config_revisions, item_count, capped) "
    "VALUES (:id, 1, 30, :cutoff, CAST(:revisions AS jsonb), 0, false)"
)
_INSERT = (
    f"INSERT INTO {_TABLE} (grant_id, holder, outcome, receipt) "
    "VALUES (:grant, :holder, :outcome, CAST(:receipt AS jsonb))"
)
_CATALOG = f"""
SELECT
    (SELECT relname || ':' || xmin::text FROM pg_class WHERE oid = '{_TABLE}'::regclass),
    (SELECT array_agg(conname || ':' || xmin::text ORDER BY conname) FROM pg_constraint
     WHERE conrelid = '{_TABLE}'::regclass),
    (SELECT array_agg(tgname || ':' || xmin::text ORDER BY tgname) FROM pg_trigger
     WHERE tgrelid = '{_TABLE}'::regclass)
"""


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


@pytest.fixture
def chronicler_039_db(postgres_container):
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_039")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def _grant(conn) -> object:
    grant = uuid4()
    conn.execute(
        text(_INSERT_GRANT),
        {"id": grant, "cutoff": datetime(2026, 10, 16, tzinfo=UTC), "revisions": _REVISIONS},
    )
    return grant


def _receipt(conn, grant, outcome: str, holder: str = _HOLDER, receipt: str = "{}") -> None:
    conn.execute(
        text(_INSERT), {"grant": grant, "holder": holder, "outcome": outcome, "receipt": receipt}
    )


def test_the_runtime_role_may_only_read_and_append(chronicler_039_db) -> None:
    _, engine = chronicler_039_db
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
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
    }


def test_receipts_are_appended_and_complete_once(chronicler_039_db) -> None:
    _, engine = chronicler_039_db
    with engine.begin() as conn:
        grant = _grant(conn)
        _receipt(conn, grant, "blocked", receipt='{"blocked": 1}')
        _receipt(conn, grant, "blocked")
        _receipt(conn, grant, "complete", receipt='{"sanitized": 2}')

    for statement in (f"UPDATE {_TABLE} SET outcome = 'blocked'", f"DELETE FROM {_TABLE}"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))

    for grant_id, outcome, holder, receipt in (
        (grant, "complete", _HOLDER, "{}"),
        (grant, "forgotten", _HOLDER, "{}"),
        (grant, "blocked", "Switchboard Inbox", "{}"),
        (uuid4(), "complete", _HOLDER, "{}"),
        (grant, "blocked", _HOLDER, "[]"),
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _receipt(conn, grant_id, outcome, holder, receipt)

    with engine.connect() as conn:
        outcomes = conn.execute(
            text(f"SELECT outcome FROM {_TABLE} WHERE grant_id = :g ORDER BY id"), {"g": grant}
        ).scalars()
        assert list(outcomes) == ["blocked", "blocked", "complete"]


def test_a_rerun_only_reads_the_catalog_and_receipts_block_the_downgrade(
    chronicler_039_db,
) -> None:
    db_url, engine = chronicler_039_db
    with engine.begin() as conn:
        _receipt(conn, _grant(conn), "blocked")
    with engine.connect() as conn:
        installed = tuple(conn.execute(text(_CATALOG)).one())
    command.stamp(_config(db_url), "chronicler_038")
    command.upgrade(_config(db_url), "chronicler@chronicler_039")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed, "re-run wrote the catalog"

    with pytest.raises(DBAPIError, match="receipts are recorded"):
        command.downgrade(_config(db_url), "chronicler_038")
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 1


def test_an_empty_table_downgrades_and_keeps_the_grants(chronicler_039_db) -> None:
    db_url, engine = chronicler_039_db
    with engine.begin() as conn:
        _grant(conn)
    command.downgrade(_config(db_url), "chronicler_038")
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT to_regclass('{_TABLE}')")).scalar_one() is None
        assert (
            conn.execute(
                text("SELECT count(*) FROM chronicler.location_retention_grants")
            ).scalar_one()
            == 1
        )
