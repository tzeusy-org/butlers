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
from fastapi import HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.routers.data_ops import _build_export_zip, _memory_export_entries
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None


def _manager(pools, schemas, memory_butlers):
    """A REAL DatabaseManager (not a double) so the property/method seam is exercised.

    ``butler_names`` is a property on the real manager; a double that defines it as a
    method hid a TypeError that made the production export 500.
    """
    db = DatabaseManager()
    for name, pool in pools.items():
        db._pools[name] = pool
        db._butler_schemas[name] = schemas[name]
        db._butler_memory_schema_overrides[name] = None
    if memory_butlers is not None:
        for name in pools:
            db._butler_modules[name] = frozenset({"memory"} if name in memory_butlers else set())
    return db


def _pool(rows):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)
    return pool


def _files(zip_bytes: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {n: zf.read(n).decode() for n in zf.namelist()}


def test_memory_entries_are_per_butler_schema_qualified_and_skip_non_memory():
    db = _manager(
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


def test_memory_entries_use_all_butlers_without_module_metadata_via_real_property():
    db = _manager({"general": _pool([])}, {"general": "general"}, None)
    assert {e[2] for e in _memory_export_entries(db)} == {"general"}


def test_memory_entries_fail_fast_on_empty_roster():
    with pytest.raises(HTTPException) as exc:
        _memory_export_entries(_manager({}, {}, None))
    assert exc.value.status_code == 500
    with pytest.raises(HTTPException):
        _memory_export_entries(_manager({"messenger": _pool([])}, {"messenger": "m"}, []))


def test_memory_entries_503_when_a_configured_memory_butler_has_no_pool():
    db = _manager({"alpha": _pool([])}, {"alpha": "alpha"}, ["alpha"])
    db.register_configured_modules("alpha", frozenset({"memory"}))
    db.register_configured_modules("beta", frozenset({"memory"}))
    with pytest.raises(HTTPException) as exc:
        _memory_export_entries(db)
    assert exc.value.status_code == 503
    assert "beta" in exc.value.detail and "alpha" not in exc.value.detail


def test_memory_entries_ok_when_every_configured_memory_butler_is_pooled():
    db = _manager({"alpha": _pool([])}, {"alpha": "alpha"}, ["alpha"])
    db.register_configured_modules("alpha", frozenset({"memory"}))
    db.register_configured_modules("gamma", frozenset({"calendar"}))
    assert {e[2] for e in _memory_export_entries(db)} == {"alpha"}


def test_memory_entries_reject_unsafe_schema_identifier():
    db = _manager({"general": _pool([])}, {"general": 'x"; DROP SCHEMA y; --'}, ["general"])
    with pytest.raises(ValueError):
        _memory_export_entries(db)


async def test_memory_scope_queries_each_butlers_own_pool_not_memory_schema():
    general = _pool([{"id": "g1", "embedding": [0.1]}])
    health = _pool([{"id": "h1"}])
    db = _manager(
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
    db = _manager({"general": _pool([])}, {"general": "general"}, ["general"])
    files = _files(await _build_export_zip(db.pool("general"), "all", db=db))
    assert "general_memory_rules.ndjson" in files
    assert "public_audit_log.ndjson" in files


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestMemoryExportRealSchemas:
    """Two butlers with memory in their own schemas of real migrated databases."""

    @pytest.fixture(scope="class")
    def database_urls(self, postgres_container) -> dict[str, str]:
        # create_migrated_test_db is synchronous (it calls asyncio.run internally), so it
        # must run in a sync fixture, never inside the running event loop.
        return {
            butler: create_migrated_test_db(
                postgres_container,
                migration_db_name(),
                chains=["core", "memory"],
                schemas={"memory": butler},
            )
            for butler in ("alpha", "beta")
        }

    @pytest.fixture
    async def pools(self, database_urls: dict[str, str]):
        made = {
            butler: await asyncpg.create_pool(
                url, min_size=1, max_size=2, init=register_jsonb_codec
            )
            for butler, url in database_urls.items()
        }
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

        db = _manager(pools, {"alpha": "alpha", "beta": "beta"}, ["alpha", "beta"])
        files = _files(await _build_export_zip(pools["alpha"], "memory", db=db))

        for butler in ("alpha", "beta"):
            rows = [
                json.loads(line) for line in files[f"{butler}_memory_episodes.ndjson"].splitlines()
            ]
            assert [r["content"] for r in rows] == [f"{butler} episode"]
            assert f"{butler}_memory_facts.ndjson" in files
