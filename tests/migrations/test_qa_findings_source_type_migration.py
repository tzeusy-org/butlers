"""Regression tests for QA findings source-type schema drift."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from alembic import command
from butlers.migrations import (
    _build_alembic_config,
    get_chain_head,
    run_migrations,
)
from butlers.modules.qa import _KNOWN_SOURCES
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration

_TOOL_CALL_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_139_qa_findings_tool_call_failures_source.py"
)
_INFRA_STATE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_170_qa_findings_infra_state_source.py"
)


def _load_migration(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migrated_core_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.mark.parametrize("source_type", sorted(_KNOWN_SOURCES))
def test_core_migrations_accept_known_qa_sources(
    migrated_core_db_url: str,
    source_type: str,
) -> None:
    """The migrated schema must persist every source accepted by QA config."""

    async def _exercise() -> None:
        pool = await asyncpg.create_pool(migrated_core_db_url, min_size=1, max_size=2)
        try:
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
                    occurrence_count, first_seen, last_seen, structured_evidence
                )
                VALUES ($1, $2, $3, 'switchboard',
                        2, 'ValueError', 'QA source finding', 'qa:discovery',
                        1, $4, $4, $5)
                RETURNING id
                """,
                patrol_id,
                uuid.uuid4().hex + uuid.uuid4().hex,
                source_type,
                now,
                json.dumps({"source": source_type}),
            )
            assert finding_id is not None
        finally:
            await pool.close()

    asyncio.run(_exercise())


def test_new_schema_replay_preserves_newer_public_source_types(postgres_container) -> None:
    """A late schema can replay core after shared QA data reached a later vocabulary."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))

    async def _seed_and_replay() -> None:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            patrol_id = await pool.fetchval(
                """
                INSERT INTO public.qa_patrols (status, started_at, completed_at)
                VALUES ('running', now(), now())
                RETURNING id
                """
            )
            now = datetime.now(UTC)
            for source_type in sorted(_KNOWN_SOURCES):
                await pool.execute(
                    """
                    INSERT INTO public.qa_findings (
                        patrol_id, fingerprint, source_type, source_butler,
                        severity, exception_type, event_summary, call_site,
                        occurrence_count, first_seen, last_seen
                    )
                    VALUES ($1, $2, $3, 'switchboard',
                            1, 'QaSourceFinding', 'QA source finding', 'qa:discovery',
                            1, $4, $4)
                    """,
                    patrol_id,
                    uuid.uuid4().hex + uuid.uuid4().hex,
                    source_type,
                    now,
                )
        finally:
            await pool.close()

        await run_migrations(db_url, chain="core", schema="concierge")

        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            persisted_sources = {
                row["source_type"]
                for row in await pool.fetch(
                    "SELECT source_type FROM public.qa_findings WHERE patrol_id = $1",
                    patrol_id,
                )
            }
            assert persisted_sources == _KNOWN_SOURCES
            assert {
                row["version_num"]
                for row in await pool.fetch("SELECT version_num FROM concierge.alembic_version")
            } == {get_chain_head("core")}
        finally:
            await pool.close()

    asyncio.run(_seed_and_replay())


def test_downgrade_preserves_persisted_infra_state_findings(postgres_container) -> None:
    """One-revision rollback must not delete, relabel, or reject existing findings."""
    db_name = migration_db_name()
    infra_state_migration = _load_migration("core_170", _INFRA_STATE_MIGRATION_PATH)
    # Stop at the migration under test: later core revisions install privileged
    # boundaries whose rollback is deliberately bootstrap-only, so migrating to
    # head first would make this one-revision rollback walk them.
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core"],
        revisions={"core": infra_state_migration.revision},
    )

    async def _insert() -> uuid.UUID:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            patrol_id = await pool.fetchval(
                """
                INSERT INTO public.qa_patrols (status, started_at, completed_at)
                VALUES ('running', now(), now())
                RETURNING id
                """
            )
            now = datetime.now(UTC)
            return await pool.fetchval(
                """
                INSERT INTO public.qa_findings (
                    patrol_id, fingerprint, source_type, source_butler,
                    severity, exception_type, event_summary, call_site,
                    occurrence_count, first_seen, last_seen
                )
                VALUES ($1, $2, 'infra_state', 'switchboard',
                        1, 'ConnectorOffline', 'Connector steam is offline', 'connector:steam',
                        1, $3, $3)
                RETURNING id
                """,
                patrol_id,
                uuid.uuid4().hex + uuid.uuid4().hex,
                now,
            )
        finally:
            await pool.close()

    finding_id = asyncio.run(_insert())
    command.downgrade(
        _build_alembic_config(
            migration_bootstrap_db_url(postgres_container, db_name), chains=["core"]
        ),
        "core_169",
    )

    async def _verify() -> None:
        pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
        try:
            row = await pool.fetchrow(
                "SELECT source_type FROM public.qa_findings WHERE id = $1",
                finding_id,
            )
            assert row is not None
            assert row["source_type"] == "infra_state"

            constraint = await pool.fetchval(
                """
                SELECT pg_get_constraintdef(oid, true)
                FROM pg_constraint
                WHERE conrelid = 'public.qa_findings'::regclass
                  AND conname = 'ck_qa_findings_source_type'
                """
            )
            assert "infra_state" in constraint
        finally:
            await pool.close()

    asyncio.run(_verify())
