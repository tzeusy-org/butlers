"""Memory-scope owner data export reads each butler's own memory schema (bu-8ptawc).

``memory`` / ``all`` used to select from a ``memory`` schema that no migration
creates.  The scope is now built from the butlers that run the memory module,
each queried through its own pool and effective memory schema.

The unit tests need no database.  ``TestMemoryExportRealSchemas`` runs against
real migrated databases and therefore executes in CI's integration shards only.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.api.routers.data_ops import _build_export_zip, _memory_export_entries
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None


class _FakeDb:
    """Minimal DatabaseManager surface used by the export."""

    def __init__(self, pools, schemas, memory_butlers):
        self._pools = pools
        self._schemas = schemas
        self._memory_butlers = memory_butlers

    def butler_names(self):
        return list(self._pools)

    def butlers_with_module(self, name):
        assert name == "memory"
        return self._memory_butlers

    def memory_schema_for_butler(self, name):
        return self._schemas[name]

    def pool(self, name):
        return self._pools[name]


def _pool(rows):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    return pool


def _files(zip_bytes: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {n: zf.read(n).decode() for n in zf.namelist()}


def test_memory_entries_are_per_butler_schema_qualified_and_skip_non_memory():
    db = _FakeDb(
        {"general": _pool([]), "health": _pool([]), "messenger": _pool([])},
        {"general": "general", "health": "health_mem", "messenger": "messenger"},
        ["general", "health"],
    )
    entries = _memory_export_entries(db)
    assert [(e[0], e[2]) for e in entries] == [
        ("general.memory.facts", "general"),
        ("general.memory.rules", "general"),
        ("general.memory.episodes", "general"),
        ("health.memory.facts", "health"),
        ("health.memory.rules", "health"),
        ("health.memory.episodes", "health"),
    ]
    assert entries[3][1] == 'SELECT * FROM "health_mem"."facts" ORDER BY created_at'
    assert not any("memory." in e[1] for e in entries)


def test_memory_entries_fall_back_to_all_butlers_without_module_metadata():
    db = _FakeDb({"general": _pool([])}, {"general": "general"}, None)
    assert {e[2] for e in _memory_export_entries(db)} == {"general"}


def test_memory_entries_reject_unsafe_schema_identifier():
    db = _FakeDb({"general": _pool([])}, {"general": 'x"; DROP SCHEMA y; --'}, ["general"])
    with pytest.raises(ValueError):
        _memory_export_entries(db)


async def test_memory_scope_queries_each_butlers_own_pool_not_memory_schema():
    general = _pool([{"id": "g1", "embedding": [0.1]}])
    health = _pool([{"id": "h1"}])
    db = _FakeDb(
        {"general": general, "health": health},
        {"general": "general", "health": "health"},
        ["general", "health"],
    )
    switchboard = _pool([])
    files = _files(await _build_export_zip(switchboard, "memory", db=db))

    assert "general_memory_facts.ndjson" in files
    assert "health_memory_episodes.ndjson" in files
    assert json.loads(files["general_memory_facts.ndjson"].splitlines()[0]) == {"id": "g1"}
    switchboard.fetch.assert_not_awaited()
    for pool in (general, health):
        for call in pool.fetch.await_args_list:
            assert "FROM memory." not in call.args[0]


async def test_all_scope_includes_memory_per_butler_plus_static_scopes():
    db = _FakeDb({"general": _pool([])}, {"general": "general"}, ["general"])
    files = _files(await _build_export_zip(db.pool("general"), "all", db=db))
    assert "general_memory_rules.ndjson" in files
    assert "public_audit_log.ndjson" in files


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestMemoryExportRealSchemas:
    """Two butlers with memory in their own schemas of real migrated databases."""

    @pytest.fixture
    async def pools(self, postgres_container):
        made = {}
        for butler in ("alpha", "beta"):
            url = create_migrated_test_db(
                postgres_container,
                migration_db_name(),
                chains=["core", "memory"],
                schemas={"memory": butler},
            )
            pool = await asyncpg.create_pool(url, min_size=1, max_size=2, init=register_jsonb_codec)
            made[butler] = pool
        yield made
        for pool in made.values():
            await pool.close()

    async def test_export_reads_each_butlers_memory_and_legacy_schema_is_absent(self, pools):
        for butler, pool in pools.items():
            await pool.execute(
                f'INSERT INTO "{butler}".episodes (butler, content, importance) '
                "VALUES ($1, $2, 5.0)",
                butler,
                f"{butler} episode",
            )
        # The defect: no `memory` schema exists in a real deployment.
        with pytest.raises(asyncpg.UndefinedTableError):
            await pools["alpha"].fetch("SELECT * FROM memory.facts")

        db = _FakeDb(pools, {"alpha": "alpha", "beta": "beta"}, ["alpha", "beta"])
        files = _files(await _build_export_zip(pools["alpha"], "memory", db=db))

        for butler in ("alpha", "beta"):
            rows = [
                json.loads(line) for line in files[f"{butler}_memory_episodes.ndjson"].splitlines()
            ]
            assert [r["content"] for r in rows] == [f"{butler} episode"]
            assert f"{butler}_memory_facts.ndjson" in files
