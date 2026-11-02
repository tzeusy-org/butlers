"""Migrated, runtime-role signed exports. REQ-dashboard-permissions-001.

Synthetic domain client credentials exercise the real owner middleware; owner
ceremony/CSRF coverage remains in its retained suite. No public.runtime_config
is created, and no migration or production grant is changed by this fixture.
"""

import asyncio
import io
import json
import zipfile
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.routers.data_ops import _decrypt_export
from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
async def export_sources(postgres_container):
    name = migration_db_name()
    url = await asyncio.to_thread(
        create_migrated_test_db, postgres_container, name, ["core"], {"core": "general"}
    )
    await run_migrations(url, chain="core", schema="switchboard")
    for schema in ("general_memory", "switchboard"):
        await run_migrations(url, chain="memory", schema=schema)
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, name),
        urlparse(url).username,
    )
    admin = await asyncpg.create_pool(url, min_size=1, max_size=2, init=register_jsonb_codec)
    # An explicitly configured private memory override needs its own role's
    # access. Only these three owned relations are granted in this disposable
    # fixture, never another butler's schema or a foreign domain table.
    await admin.execute("GRANT USAGE ON SCHEMA general_memory TO butler_general_rw")
    await admin.execute(
        "GRANT SELECT ON general_memory.facts, general_memory.rules, "
        "general_memory.episodes TO butler_general_rw"
    )
    db = DatabaseManager()
    for butler, memory in (("general", "general_memory"), ("switchboard", "switchboard")):

        async def setup(conn, role=f"butler_{butler}_rw"):
            await conn.execute(f"SET ROLE {role}")

        pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=2,
            init=register_jsonb_codec,
            setup=setup,
            server_settings={"search_path": "public"},
        )
        assert await pool.fetchval("SELECT current_user") == f"butler_{butler}_rw"
        db._pools[butler] = pool
        db._butler_schemas[butler] = butler
        db._butler_memory_schema_overrides[butler] = memory
        db._butler_modules[butler] = frozenset({"memory"})
        db.register_configured_modules(butler, frozenset({"memory"}))
        await admin.execute(
            f'INSERT INTO "{butler}".runtime_config (butler_name) VALUES ($1)', butler
        )
        await admin.execute(
            f'INSERT INTO "{memory}".facts (subject, predicate, content) VALUES ($1,$2,$3)',
            butler,
            "export_probe",
            f"{butler} fact",
        )
        await admin.execute(f'INSERT INTO "{memory}".rules (content) VALUES ($1)', f"{butler} rule")
        await admin.execute(
            f'INSERT INTO "{memory}".episodes (butler, content) VALUES ($1,$2)',
            butler,
            f"{butler} episode",
        )
    assert await admin.fetchval("SELECT to_regclass('public.runtime_config')") is None
    try:
        yield db, admin
    finally:
        for pool in db._pools.values():
            await pool.close()
        await admin.close()


async def _signed_download(db):
    app = create_authenticated_domain_app()
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        issued = await client.post("/api/data/export", json={"scope": "all"})
        assert issued.status_code == 200, issued.text
        return await client.get("/api" + issued.json()["data"]["signed_url"])


async def test_all_export_matches_migrated_scope_union(export_sources, monkeypatch):
    db, admin = export_sources
    key = bytes.fromhex("11" * 32)
    monkeypatch.setenv("DASHBOARD_EXPORT_ENCRYPTION_KEY", key.hex())
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("DASHBOARD_EXPORT_SECRET", "synthetic-scope-export-signing-key")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await db.pool("general").fetch("SELECT * FROM switchboard.runtime_config")
    response = await _signed_download(db)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    with zipfile.ZipFile(io.BytesIO(_decrypt_export(response.content, key=key))) as zf:
        expected = {
            f"{butler}_memory_{rel}.ndjson"
            for butler in db.butler_names
            for rel in ("facts", "rules", "episodes")
        } | {
            f"public_{rel}.ndjson"
            for rel in ("runtime_config", "audit_log", "model_catalog", "permissions")
        }
        assert set(zf.namelist()) == expected
        assert len(zf.namelist()) == len(expected)
        for butler in db.butler_names:
            for rel, suffix in (("facts", "fact"), ("rules", "rule"), ("episodes", "episode")):
                rows = [
                    json.loads(line)
                    for line in zf.read(f"{butler}_memory_{rel}.ndjson").splitlines()
                ]
                assert [r["content"] for r in rows] == [f"{butler} {suffix}"]
                assert all("embedding" not in row and "search_vector" not in row for row in rows)
        runtime = [
            json.loads(line) for line in zf.read("public_runtime_config.ndjson").splitlines()
        ]
        assert [row["butler_name"] for row in runtime] == ["general", "switchboard"]
        assert all("id" not in row for row in runtime)
        for relation in ("audit_log", "model_catalog", "permissions"):
            order = "butler, permission" if relation == "permissions" else "id"
            native = await admin.fetch(f"SELECT * FROM public.{relation} ORDER BY {order}")
            expected_rows = json.loads(
                json.dumps(
                    [
                        {
                            k: v
                            for k, v in dict(row).items()
                            if k not in {"embedding", "search_vector", "description_embedding"}
                        }
                        for row in native
                    ],
                    default=str,
                )
            )
            archived = [
                json.loads(line) for line in zf.read(f"public_{relation}.ndjson").splitlines()
            ]
            assert archived == expected_rows
    assert await admin.fetchval("SELECT to_regclass('public.runtime_config')") is None


async def test_config_source_loss_never_returns_partial_export(export_sources, monkeypatch):
    db, admin = export_sources
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("DASHBOARD_EXPORT_SECRET", "synthetic-scope-export-signing-key")
    db.register_configured_modules("unavailable", frozenset())
    try:
        response = await _signed_download(db)
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]
    finally:
        db._configured_butler_modules.pop("unavailable")
    # General sorts first and remains readable. The later own source fails;
    # neither a successful partial ZIP nor a public-schema retry is allowed.
    assert (
        await db.pool("general").fetchval("SELECT butler_name FROM general.runtime_config")
        == "general"
    )
    for operation, restore in (
        (
            "ALTER TABLE switchboard.runtime_config RENAME TO runtime_config_unavailable",
            "ALTER TABLE switchboard.runtime_config_unavailable RENAME TO runtime_config",
        ),
        (
            "REVOKE SELECT ON switchboard.runtime_config FROM butler_switchboard_rw",
            "GRANT SELECT ON switchboard.runtime_config TO butler_switchboard_rw",
        ),
    ):
        await admin.execute(operation)
        try:
            response = await _signed_download(db)
            assert response.status_code == 500
            assert response.headers["content-type"] == "application/json"
            assert "unable to fetch table public.runtime_config" in response.json()["detail"]
        finally:
            await admin.execute(restore)
