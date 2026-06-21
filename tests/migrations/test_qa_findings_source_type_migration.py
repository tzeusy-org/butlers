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

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_139_qa_findings_tool_call_failures_source.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_139", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_revision_chain() -> None:
    mod = _load_migration()
    assert mod.revision == "core_139"
    assert mod.down_revision == "core_138"


@pytest.fixture(scope="module")
def migrated_core_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


def test_core_migrations_accept_tool_call_failures_findings(migrated_core_db_url: str) -> None:
    """A migrated core schema must persist findings from the tool-call source."""

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
                VALUES ($1, $2, 'tool_call_failures', 'education',
                        2, 'ValueError', 'ValueError: No flow found', 'route',
                        1, $3, $3, $4)
                RETURNING id
                """,
                patrol_id,
                uuid.uuid4().hex + uuid.uuid4().hex,
                now,
                json.dumps({"source": "tool_call_failures", "session_ids": [str(uuid.uuid4())]}),
            )
            assert finding_id is not None
        finally:
            await pool.close()

    asyncio.run(_exercise())
