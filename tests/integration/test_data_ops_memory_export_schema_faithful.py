"""Real-Postgres proof that the data-ops ``memory`` export scope reads
``memory.facts`` / ``memory.rules`` / ``memory.episodes`` schema-qualified
(bu-4rif7).

``_build_export_zip(pool, "memory")`` runs the three hardcoded
``SELECT * FROM memory.<table>`` queries in ``data_ops._SCOPE_MAP``. Today
``tests/api/test_data_ops.py`` covers this with 24 mock-pool cases and zero
real-Postgres coverage, so an accidental un-qualification would pass unit tests
but 500 in production (the #2598 mocked-green / integration-red class).

This provisions the memory chain into a real ``memory`` schema and drives the
export through a pool scoped to ``public`` only — the memory tables are NOT on
the search_path, so all three reads resolve solely because they are qualified.
Un-qualify any of them and the export raises (HTTPException 500 / the fetch's
``UndefinedTableError``).
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile

import asyncpg
import pytest

from butlers.api.routers.data_ops import _build_export_zip
from butlers.db import register_jsonb_codec
from butlers.modules.memory.storage import store_fact
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.modules.memory._test_helpers import make_embedding_engine_mock

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def memory_db_url(postgres_container) -> str:
    # data_ops hardcodes the ``memory.`` schema; provision the memory chain there
    # (core into public).
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
        schemas={"core": "public", "memory": "memory"},
    )


@pytest.fixture
async def public_pool(memory_db_url: str) -> asyncpg.Pool:
    """``public``-only search_path — memory.* resolves only via qualification."""
    p = await asyncpg.create_pool(
        memory_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "public"},
    )
    yield p
    await p.close()


@pytest.fixture
async def memory_pool(memory_db_url: str) -> asyncpg.Pool:
    """Seed-side pool scoped to the memory schema (store_fact writes unqualified)."""
    p = await asyncpg.create_pool(
        memory_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "memory,public"},
    )
    yield p
    await p.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_memory_export_reads_memory_schema_under_public_search_path(
    public_pool: asyncpg.Pool, memory_pool: asyncpg.Pool
) -> None:
    # Sanity: the public-only export pool cannot see the table unqualified.
    with pytest.raises(asyncpg.UndefinedTableError):
        await public_pool.fetch("SELECT id FROM facts")

    # Seed one fact into memory.facts via the memory-search_path pool.
    engine = make_embedding_engine_mock()
    result = await store_fact(
        memory_pool,
        "day-2026-07-15",
        "coverage_probe",
        "A fact that the data-ops memory export must surface.",
        engine,
        source_butler="general",
    )
    assert result["id"] is not None

    # Build the export through the public-only pool. This runs all three
    # `SELECT * FROM memory.<table>` reads; it only succeeds because they are
    # schema-qualified (facts, rules AND episodes each resolve, empty or not).
    zip_bytes = await _build_export_zip(public_pool, "memory")

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert {"memory_facts.ndjson", "memory_rules.ndjson", "memory_episodes.ndjson"} <= names
        facts_lines = [
            json.loads(line)
            for line in zf.read("memory_facts.ndjson").decode().splitlines()
            if line.strip()
        ]

    # teeth: the seeded fact came back through the qualified read.
    assert any(row.get("predicate") == "coverage_probe" for row in facts_lines)
