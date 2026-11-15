"""chronicler_033 turns each open member's single carry key into a set of keys.

Seeds an open member at chronicler_032, applies the real upgrade, and checks that the key is
carried over as a one-element ``carry_keys``, the old column is gone, and the new check refuses
an empty set or an empty key.

Issue: bu-s11n0s.7 (slice 2, PR-B)
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from uuid import uuid4

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

_INSERT_032 = (
    "INSERT INTO chronicler.location_projection_open_members (adapter_name, carry_key, "
    "raw_source_id, logical_source_key, retention_at, joined_batch_id) "
    "VALUES ('owntracks.points', 'movement:owntracks:alice', :raw, 'k', :at, :batch)"
)
_INSERT_033 = (
    "INSERT INTO chronicler.location_projection_open_members (adapter_name, carry_keys, "
    "raw_source_id, logical_source_key, retention_at, joined_batch_id) "
    "VALUES ('owntracks.points', CAST(:keys AS text[]), :raw, 'k', :at, :batch)"
)


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


@pytest.fixture
def chronicler_032_db(postgres_container):
    # chronicler_030 refuses to run until the private memory storage exists, so stop before
    # it, prepare memory, then continue to the seeding revision.
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_032")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def test_a_single_carry_key_becomes_a_one_element_key_set(chronicler_032_db) -> None:
    db_url, engine = chronicler_032_db
    raw_id, at = uuid4(), datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
    with engine.connect() as conn:
        conn.execute(text(_INSERT_032), {"raw": raw_id, "at": at, "batch": uuid4()})

    from butlers.migrations import run_migrations

    asyncio.run(run_migrations(db_url, chain="chronicler", schema="chronicler"))

    with engine.connect() as conn:
        keys = conn.execute(
            text(
                "SELECT carry_keys FROM chronicler.location_projection_open_members "
                "WHERE raw_source_id = :raw"
            ),
            {"raw": raw_id},
        ).scalar_one()
        columns = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'chronicler' "
                    "AND table_name = 'location_projection_open_members'"
                )
            ).scalars()
        )
    assert keys == ["movement:owntracks:alice"]
    assert "carry_key" not in columns

    for bad in ("{}", '{""}'):
        with pytest.raises(IntegrityError):
            with engine.connect() as conn:
                conn.execute(
                    text(_INSERT_033), {"keys": bad, "raw": uuid4(), "at": at, "batch": uuid4()}
                )
