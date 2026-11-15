"""chronicler_037: later retention grant states, appended rather than updated.

A grant row is written ``prepared`` and never changes (chronicler_036). Every later state the
grant reaches is a new row in ``location_retention_grant_states``. Applies the real upgrade at
chronicler_036 and checks, against Postgres: the runtime role may only read and append, the
table refuses UPDATE and DELETE, a state names an existing grant, a grant is ready, reconciled
or failed at most once (``unknown`` may repeat while a lost acknowledgement is retried), a
re-run only reads the catalog, and the downgrade drops the table but keeps chronicler_036.

Issue: bu-s11n0s.7 (slice 4b, requirement 5)
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
_TABLE = "chronicler.location_retention_grant_states"
_GUARD = "chronicler.location_retention_grant_append_only()"
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
_INSERT_STATE = f"INSERT INTO {_TABLE} (grant_id, state, receipt) VALUES (:grant, :state, CAST(:receipt AS jsonb))"
_CATALOG = f"""
SELECT
    (SELECT relname || ':' || xmin::text FROM pg_class WHERE oid = '{_TABLE}'::regclass),
    (SELECT array_agg(conname || ':' || xmin::text ORDER BY conname) FROM pg_constraint
     WHERE conrelid = '{_TABLE}'::regclass),
    (SELECT array_agg(tgname || ':' || xmin::text ORDER BY tgname) FROM pg_trigger
     WHERE tgrelid = '{_TABLE}'::regclass),
    (SELECT array_agg(indexrelid::regclass::text ORDER BY indexrelid::regclass::text)
     FROM pg_index WHERE indrelid = '{_TABLE}'::regclass)
"""


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


@pytest.fixture
def chronicler_037_db(postgres_container):
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_037")
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


def _state(conn, grant: object, state: str, receipt: str = "{}") -> None:
    conn.execute(text(_INSERT_STATE), {"grant": grant, "state": state, "receipt": receipt})


def test_the_runtime_role_may_only_read_and_append(chronicler_037_db) -> None:
    _, engine = chronicler_037_db
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


def test_states_are_appended_once_and_never_changed(chronicler_037_db) -> None:
    _, engine = chronicler_037_db
    with engine.begin() as conn:
        grant = _grant(conn)
        _state(conn, grant, "ready", '{"holders": 0}')
        _state(conn, grant, "unknown")
        _state(conn, grant, "unknown")
        _state(conn, grant, "reconciled", '{"deleted": 0}')

    for statement in (f"UPDATE {_TABLE} SET state = 'failed'", f"DELETE FROM {_TABLE}"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))

    # A once-only state again, an unknown state, a state without its grant, a non-object receipt.
    for grant_id, state, receipt in (
        (grant, "ready", "{}"),
        (grant, "reconciled", "{}"),
        (grant, "prepared", "{}"),
        (grant, "deleted", "{}"),
        (uuid4(), "ready", "{}"),
        (grant, "failed", "[]"),
    ):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _state(conn, grant_id, state, receipt)

    with engine.connect() as conn:
        states = conn.execute(
            text(f"SELECT state FROM {_TABLE} WHERE grant_id = :g ORDER BY id"), {"g": grant}
        ).scalars()
        assert list(states) == ["ready", "unknown", "unknown", "reconciled"]


def test_a_rerun_only_reads_the_catalog_and_the_downgrade_keeps_the_grants(
    chronicler_037_db,
) -> None:
    db_url, engine = chronicler_037_db
    with engine.begin() as conn:
        _state(conn, _grant(conn), "ready")
    with engine.connect() as conn:
        installed = tuple(conn.execute(text(_CATALOG)).one())
    command.stamp(_config(db_url), "chronicler_036")
    command.upgrade(_config(db_url), "chronicler@chronicler_037")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed, "re-run wrote the catalog"
        assert conn.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 1

    command.downgrade(_config(db_url), "chronicler_036")
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT to_regclass('{_TABLE}')")).scalar_one() is None
        assert conn.execute(text(f"SELECT to_regprocedure('{_GUARD}')")).scalar_one() is not None
        assert (
            conn.execute(
                text("SELECT count(*) FROM chronicler.location_retention_grants")
            ).scalar_one()
            == 1
        )
