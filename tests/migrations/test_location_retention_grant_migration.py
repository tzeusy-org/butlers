"""chronicler_036: prepared retention grants and their items.

Applies the real upgrade at chronicler_035 and checks, against Postgres: the runtime role may
only read and append, both tables refuse UPDATE and DELETE, a raw row is granted at most once
and never without its grant, a re-run of the revision only reads the catalog, and the downgrade
drops both tables and the guard.

Issue: bu-s11n0s.7 (slice 4a)
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
_TABLES = ("location_retention_grants", "location_retention_grant_items")
_CUTOFF = datetime(2026, 10, 16, tzinfo=UTC)
_DIGEST = "v1:" + "a" * 64
_REVISIONS = json.dumps(
    dict.fromkeys(
        ("owntracks.points", "owntracks.place_cluster", "owntracks.ssid_presence"), _DIGEST
    )
)

_INSERT_GRANT = (
    "INSERT INTO chronicler.location_retention_grants (id, policy_version, retention_days, "
    "cutoff, config_revisions, item_count, capped) "
    "VALUES (:id, 1, 30, :cutoff, CAST(:revisions AS jsonb), 1, false)"
)
_INSERT_ITEM = (
    "INSERT INTO chronicler.location_retention_grant_items (grant_id, raw_source_id, "
    "logical_source_key, retention_at, content_digest) "
    "VALUES (:grant, :raw, 'owntracks:alice:1:location', :cutoff, :digest)"
)
_COUNTS = (
    "SELECT (SELECT count(*) FROM chronicler.location_retention_grants), "
    "(SELECT count(*) FROM chronicler.location_retention_grant_items)"
)
_GUARD = "chronicler.location_retention_grant_append_only()"
# Every catalog row the revision writes. A catalog-read-only re-run leaves each xmin unchanged.
_CATALOG = f"""
SELECT
    (SELECT array_agg(relname || ':' || xmin::text ORDER BY relname) FROM pg_class
     WHERE relnamespace = 'chronicler'::regnamespace AND relname LIKE 'location_retention_grant%'),
    (SELECT array_agg(conname || ':' || xmin::text ORDER BY conname) FROM pg_constraint
     WHERE conrelid IN ('chronicler.location_retention_grants'::regclass,
                        'chronicler.location_retention_grant_items'::regclass)),
    (SELECT array_agg(tgname || ':' || xmin::text ORDER BY tgname) FROM pg_trigger
     WHERE tgrelid IN ('chronicler.location_retention_grants'::regclass,
                       'chronicler.location_retention_grant_items'::regclass)),
    (SELECT xmin::text FROM pg_proc WHERE oid = '{_GUARD}'::regprocedure)
"""


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


@pytest.fixture
def chronicler_036_db(postgres_container):
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
    command.upgrade(_config(db_url), "chronicler@chronicler_036")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def _grant(conn) -> object:
    grant = uuid4()
    conn.execute(text(_INSERT_GRANT), {"id": grant, "cutoff": _CUTOFF, "revisions": _REVISIONS})
    return grant


def test_the_runtime_role_may_only_read_and_append(chronicler_036_db) -> None:
    _, engine = chronicler_036_db
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


def test_both_tables_are_append_only_and_a_row_is_granted_once(chronicler_036_db) -> None:
    _, engine = chronicler_036_db
    raw = uuid4()
    with engine.begin() as conn:
        grant = _grant(conn)
        conn.execute(
            text(_INSERT_ITEM), {"grant": grant, "raw": raw, "cutoff": _CUTOFF, "digest": _DIGEST}
        )

    for statement in (
        "UPDATE chronicler.location_retention_grants SET item_count = 9",
        "DELETE FROM chronicler.location_retention_grants",
        "UPDATE chronicler.location_retention_grant_items SET content_digest = 'x'",
        "DELETE FROM chronicler.location_retention_grant_items",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))

    # The same raw row in a later grant, an item without its grant, or a malformed digest.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            later = _grant(conn)
            conn.execute(
                text(_INSERT_ITEM),
                {"grant": later, "raw": raw, "cutoff": _CUTOFF, "digest": _DIGEST},
            )
    for grant_id, digest in ((uuid4(), _DIGEST), (grant, "legacy")):
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(_INSERT_ITEM),
                    {"grant": grant_id, "raw": uuid4(), "cutoff": _CUTOFF, "digest": digest},
                )


def test_a_rerun_only_reads_the_catalog_and_the_downgrade_drops_everything(
    chronicler_036_db,
) -> None:
    db_url, engine = chronicler_036_db
    with engine.begin() as conn:
        grant = _grant(conn)
        conn.execute(
            text(_INSERT_ITEM),
            {"grant": grant, "raw": uuid4(), "cutoff": _CUTOFF, "digest": _DIGEST},
        )
    with engine.connect() as conn:
        installed = tuple(conn.execute(text(_CATALOG)).one())
    command.stamp(_config(db_url), "chronicler_035")
    command.upgrade(_config(db_url), "chronicler@chronicler_036")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed, "re-run wrote the catalog"
        assert conn.execute(text(_COUNTS)).one() == (1, 1)

    command.downgrade(_config(db_url), "chronicler_035")
    with engine.connect() as conn:
        for table in _TABLES:
            assert (
                conn.execute(text(f"SELECT to_regclass('chronicler.{table}')")).scalar_one() is None
            )
        assert conn.execute(text(f"SELECT to_regprocedure('{_GUARD}')")).scalar_one() is None
    command.upgrade(_config(db_url), "chronicler@chronicler_036")
    with engine.connect() as conn:
        assert conn.execute(text(_COUNTS)).one() == (0, 0)
