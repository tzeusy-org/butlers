"""Real-Postgres check: effective public-relation grants equal the manifest (bu-dss7he.6 S2a).

Migrates the core and Switchboard chains (the ones that create ``public`` relations),
reads every runtime role's effective privileges with ``has_table_privilege``, and
diffs them against ``butlers.db_authority.grants``. Then it replays ``init-db.sql``,
the bootstrap that re-grants blanket DML, and checks again: a rerun must not widen
any boundary row.

Every relation present must be in the manifest and match it exactly. Manifest rows
absent here (relations other chains or the WhatsApp bridge create) are tolerated;
``butlers db check-grants`` against a fully migrated database reports them.
"""

from __future__ import annotations

import shutil
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine

from butlers.db_authority.catalog import diff_public_catalog, read_public_catalog
from butlers.db_authority.grants import WA_BRIDGE_RELATIONS
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _diff(db_url: str) -> list[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            catalog = read_public_catalog(conn)
    finally:
        engine.dispose()
    assert catalog.privileges, "no public relations were read"
    return diff_public_catalog(catalog, absent_ok=WA_BRIDGE_RELATIONS)


def _replay_init_db(postgres_container, db_name: str, migration_user: str) -> None:
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    raw = engine.raw_connection()
    try:
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)", (migration_user,)
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw.close()
        engine.dispose()


def test_public_grants_equal_manifest_before_and_after_bootstrap_replay(
    postgres_container,
) -> None:
    db_name = migration_db_name()
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )
    fresh = _diff(db_url)

    _replay_init_db(postgres_container, db_name, str(urlparse(db_url).username))
    replayed = _diff(db_url)
    assert not fresh and not replayed, "\n".join(
        ["FRESH:", *fresh, "AFTER INIT-DB REPLAY:", *replayed]
    )
