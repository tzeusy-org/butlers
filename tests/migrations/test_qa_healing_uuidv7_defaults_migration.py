"""Regression tests for the qa_findings / healing_attempts UUIDv7 PK defaults.

core_147 repoints ``public.qa_findings.id`` and ``public.healing_attempts.id``
from ``gen_random_uuid()`` (v4) to ``public.uuid_generate_v7()`` (v7), matching
the documented intent and the sibling QA journal convention.
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_147_qa_healing_uuidv7_defaults.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_147", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_147"
    assert mod.down_revision == "core_146"


@pytest.fixture(scope="module")
def migrated_core_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


def test_default_generated_ids_are_uuidv7(migrated_core_db_url: str) -> None:
    """New qa_findings / healing_attempts rows receive v7 ids from the DB default."""

    async def _exercise() -> None:
        pool = await asyncpg.create_pool(migrated_core_db_url, min_size=1, max_size=2)
        try:
            # healing_attempts: no id supplied -> DB default must produce v7.
            healing_id = await pool.fetchval(
                """
                INSERT INTO public.healing_attempts (
                    fingerprint, butler_name, status, severity,
                    exception_type, call_site
                )
                VALUES ($1, 'education', 'investigating', 2,
                        'ValueError', 'route:handle')
                RETURNING id
                """,
                uuid.uuid4().hex,
            )
            assert isinstance(healing_id, uuid.UUID)
            assert healing_id.version == 7

            # qa_findings: no id supplied -> DB default must produce v7.
            patrol_id = await pool.fetchval(
                """
                INSERT INTO public.qa_patrols (status, started_at, completed_at)
                VALUES ('running', now(), now())
                RETURNING id
                """
            )
            now = datetime.now(UTC)
            finding_id = await pool.fetchval(
                """
                INSERT INTO public.qa_findings (
                    patrol_id, fingerprint, source_type, source_butler,
                    severity, exception_type, event_summary, call_site,
                    occurrence_count, first_seen, last_seen
                )
                VALUES ($1, $2, 'log_scanner', 'education',
                        2, 'ValueError', 'ValueError: boom', 'route',
                        1, $3, $3)
                RETURNING id
                """,
                patrol_id,
                uuid.uuid4().hex,
                now,
            )
            assert isinstance(finding_id, uuid.UUID)
            assert finding_id.version == 7
        finally:
            await pool.close()

    asyncio.run(_exercise())


def test_uuid_v7_default_is_time_ordered(migrated_core_db_url: str) -> None:
    """Sequential default-generated v7 ids sort in creation order (time-prefixed)."""

    async def _exercise() -> None:
        pool = await asyncpg.create_pool(migrated_core_db_url, min_size=1, max_size=2)
        try:
            ids: list[uuid.UUID] = []
            for _ in range(5):
                generated = await pool.fetchval(
                    "SELECT public.uuid_generate_v7()",
                )
                assert isinstance(generated, uuid.UUID)
                assert generated.version == 7
                # RFC-4122 variant: top two bits of the variant octet are 10.
                assert (generated.bytes[8] & 0xC0) == 0x80
                ids.append(generated)
                await asyncio.sleep(0.002)
            assert ids == sorted(ids)
        finally:
            await pool.close()

    asyncio.run(_exercise())
