"""chronicler_041: the owner's switch for raw location expiry, off by default (bu-s11n0s.75).

Applies the real upgrade at chronicler_040 and checks, against Postgres: the policy row gains
``enforced`` (``NOT NULL``, default false) and keeps its horizon and version, the runtime role's
grants are unchanged, a re-run only reads the catalog, and the downgrade refuses while the owner
has enforcement on and otherwise drops only the column.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.migrations import test_location_retention_holder_receipts_migration as _receipts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_config = _receipts._config
_POLICY = "chronicler.location_retention_policy"
_COLUMN = f"""
SELECT attname || ':' || xmin::text FROM pg_attribute
WHERE attrelid = '{_POLICY}'::regclass AND attname = 'enforced'
"""


@pytest.fixture
def chronicler_041_db(postgres_container):
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
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE {_POLICY} SET days = 7, version = 3 WHERE id = 1"))
    command.upgrade(_config(db_url), "chronicler@chronicler_041")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def test_the_switch_is_off_and_the_policy_is_kept(chronicler_041_db) -> None:
    _, engine = chronicler_041_db
    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT days, version, enforced FROM {_POLICY}")).one()
    assert tuple(row) == (7, 3, False)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(f"UPDATE {_POLICY} SET enforced = NULL"))
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT has_column_privilege('butler_chronicler_rw', :t, 'enforced', 'UPDATE')"),
            {"t": _POLICY},
        ).scalar_one()


def test_a_rerun_reads_only_and_the_downgrade_refuses_while_on(chronicler_041_db) -> None:
    db_url, engine = chronicler_041_db
    with engine.connect() as conn:
        installed = conn.execute(text(_COLUMN)).scalar_one()
    command.stamp(_config(db_url), "chronicler_040")
    command.upgrade(_config(db_url), "chronicler@chronicler_041")
    with engine.connect() as conn:
        assert conn.execute(text(_COLUMN)).scalar_one() == installed, "re-run wrote the catalog"

    with engine.begin() as conn:
        conn.execute(text(f"UPDATE {_POLICY} SET enforced = true"))
    with pytest.raises(DBAPIError, match="enforcement is on"):
        command.downgrade(_config(db_url), "chronicler_040")

    with engine.begin() as conn:
        conn.execute(text(f"UPDATE {_POLICY} SET enforced = false"))
    command.downgrade(_config(db_url), "chronicler_040")
    with engine.connect() as conn:
        assert conn.execute(text(_COLUMN)).scalar_one_or_none() is None
        assert tuple(conn.execute(text(f"SELECT days, version FROM {_POLICY}")).one()) == (7, 3)
