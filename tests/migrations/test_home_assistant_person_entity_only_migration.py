"""Regression coverage for entity-only Home Assistant person mappings.

``core_132`` made ``entity_id`` the canonical Home Assistant person mapping,
but the later removal of ``public.contacts`` left the legacy ``contact_id``
column required.  This migrated-DB regression reproduces that post-contacts
shape and protects direct operator-managed mappings without inventing any
person-to-entity identity data.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

_DOCKER_AVAILABLE = shutil.which("docker") is not None
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_179_ha_persons_contact_id_nullable.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_179", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_and_guards() -> None:
    migration = _load_migration()
    assert migration.revision == "core_179"
    assert migration.down_revision == "core_178"
    assert migration.branch_labels is None
    assert migration.depends_on is None

    sql = migration._MAKE_CONTACT_ID_NULLABLE_SQL
    assert "to_regclass('connectors.home_assistant_persons')" in sql
    assert "to_regclass('public.contacts') IS NOT NULL" in sql
    assert "column_name = 'entity_id'" in sql
    assert "column_name = 'contact_id'" in sql
    assert "is_nullable = 'NO'" in sql
    assert "FROM pg_constraint AS fk" in sql
    assert "fk.contype = 'f'" in sql
    assert "fk.convalidated" in sql
    assert "target_schema.nspname = 'public'" in sql
    assert "target_table.relname = 'entities'" in sql
    assert "source_column.attname = 'entity_id'" in sql
    assert "target_column.attname = 'id'" in sql
    assert "ALTER COLUMN contact_id DROP NOT NULL" in sql


@pytest.fixture(scope="module")
def pre_repair_core_db_url(postgres_container) -> str:
    """A real contacts-absent core database immediately before core_179."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@core_178")
    return db_url


@pytest.fixture(scope="module")
def pre_repair_core_without_entity_fk_db_url(postgres_container) -> str:
    """A second pre-core_179 database for the malformed no-FK guard case."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@core_178")
    return db_url


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_contacts_absent_schema_preserves_legacy_rows_and_allows_entity_only_mappings(
    pre_repair_core_db_url: str,
) -> None:
    """Direct mappings work while legacy contact IDs remain readable.

    The UUIDs in this test are synthetic test data only.  The regression must
    never manufacture resident identity mappings in a migration or runtime.
    """
    pre_repair_pool = await asyncpg.create_pool(pre_repair_core_db_url, min_size=1, max_size=2)
    try:
        assert await pre_repair_pool.fetchval("SELECT to_regclass('public.contacts')") is None
        assert (
            await pre_repair_pool.fetchval(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'connectors' "
                "AND table_name = 'home_assistant_persons' "
                "AND column_name = 'contact_id'"
            )
            == "NO"
        )

        legacy_entity_id = await pre_repair_pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            "ha-person-migration-legacy",
        )
        legacy_contact_id = uuid4()
        await pre_repair_pool.execute(
            "INSERT INTO connectors.home_assistant_persons "
            "(ha_entity_id, contact_id, entity_id) VALUES ($1, $2, $3)",
            "person.legacy_migration_fixture",
            legacy_contact_id,
            legacy_entity_id,
        )
    finally:
        await pre_repair_pool.close()

    command.upgrade(
        _build_alembic_config(pre_repair_core_db_url, chains=["core"]),
        "core@core_179",
    )

    pool = await asyncpg.create_pool(pre_repair_core_db_url, min_size=1, max_size=2)
    try:
        assert (
            await pool.fetchval(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'connectors' "
                "AND table_name = 'home_assistant_persons' "
                "AND column_name = 'contact_id'"
            )
            == "YES"
        )

        direct_entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            "ha-person-migration-direct",
        )
        direct_ha_entity_id = "person.entity_only_migration_fixture"
        await pool.execute(
            "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
            "VALUES ($1, $2)",
            direct_ha_entity_id,
            direct_entity_id,
        )

        replacement_entity_id = await pool.fetchval(
            "INSERT INTO public.entities (canonical_name, entity_type) "
            "VALUES ($1, 'person') RETURNING id",
            "ha-person-migration-upsert",
        )
        await pool.execute(
            """
            INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id)
            VALUES ($1, $2)
            ON CONFLICT (ha_entity_id) DO UPDATE
            SET entity_id = EXCLUDED.entity_id,
                updated_at = now()
            """,
            direct_ha_entity_id,
            replacement_entity_id,
        )
        direct_row = await pool.fetchrow(
            "SELECT contact_id, entity_id FROM connectors.home_assistant_persons "
            "WHERE ha_entity_id = $1",
            direct_ha_entity_id,
        )
        assert direct_row is not None
        assert direct_row["contact_id"] is None
        assert direct_row["entity_id"] == replacement_entity_id

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await pool.execute(
                "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
                "VALUES ($1, $2)",
                "person.invalid_entity_migration_fixture",
                uuid4(),
            )

        legacy_row = await pool.fetchrow(
            "SELECT contact_id, entity_id FROM connectors.home_assistant_persons "
            "WHERE ha_entity_id = $1",
            "person.legacy_migration_fixture",
        )
        assert legacy_row is not None
        assert legacy_row["contact_id"] == legacy_contact_id
        assert legacy_row["entity_id"] == legacy_entity_id
    finally:
        await pool.close()


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_contacts_absent_without_entity_fk_keeps_legacy_contact_requirement(
    pre_repair_core_without_entity_fk_db_url: str,
) -> None:
    """An arbitrary UUID cannot bypass the legacy anchor without an entity FK."""
    pre_repair_pool = await asyncpg.create_pool(
        pre_repair_core_without_entity_fk_db_url,
        min_size=1,
        max_size=2,
    )
    try:
        assert await pre_repair_pool.fetchval("SELECT to_regclass('public.contacts')") is None
        await pre_repair_pool.execute(
            "ALTER TABLE connectors.home_assistant_persons "
            "DROP CONSTRAINT home_assistant_persons_entity_id_fkey"
        )
        assert not await pre_repair_pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint AS fk
                JOIN pg_class AS source_table ON source_table.oid = fk.conrelid
                JOIN pg_namespace AS source_schema ON source_schema.oid = source_table.relnamespace
                JOIN pg_class AS target_table ON target_table.oid = fk.confrelid
                JOIN pg_namespace AS target_schema ON target_schema.oid = target_table.relnamespace
                JOIN pg_attribute AS source_column
                  ON source_column.attrelid = fk.conrelid
                 AND source_column.attnum = fk.conkey[1]
                WHERE fk.contype = 'f'
                  AND cardinality(fk.conkey) = 1
                  AND source_schema.nspname = 'connectors'
                  AND source_table.relname = 'home_assistant_persons'
                  AND source_column.attname = 'entity_id'
                  AND target_schema.nspname = 'public'
                  AND target_table.relname = 'entities'
            )
            """
        )
    finally:
        await pre_repair_pool.close()

    command.upgrade(
        _build_alembic_config(pre_repair_core_without_entity_fk_db_url, chains=["core"]),
        "core@core_179",
    )

    pool = await asyncpg.create_pool(
        pre_repair_core_without_entity_fk_db_url,
        min_size=1,
        max_size=2,
    )
    try:
        assert (
            await pool.fetchval(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'connectors' "
                "AND table_name = 'home_assistant_persons' "
                "AND column_name = 'contact_id'"
            )
            == "NO"
        )

        arbitrary_entity_id = uuid4()
        assert (
            await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.entities WHERE id = $1)",
                arbitrary_entity_id,
            )
            is False
        )
        with pytest.raises(asyncpg.NotNullViolationError):
            await pool.execute(
                "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
                "VALUES ($1, $2)",
                "person.no_entity_fk_migration_fixture",
                arbitrary_entity_id,
            )
    finally:
        await pool.close()
