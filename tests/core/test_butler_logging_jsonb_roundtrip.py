"""Real-Postgres regression: ButlerLogger metadata must not double-encode JSONB.

bu-cymc4: ``ButlerLogger._write()`` used to pre-serialize ``metadata`` with
``json.dumps()`` and bind it with an explicit ``$N::jsonb`` cast. Every
asyncpg pool in this codebase registers a JSONB type codec
(``register_jsonb_codec``, src/butlers/db.py) whose encoder calls
``json.dumps()`` itself, so the old code path double-encoded metadata into a
jsonb-typed STRING instead of an OBJECT (see
tests/relationship/test_jsonb_codec.py). The mocked-pool unit tests in
tests/core/test_butler_logging.py only assert on the Python value handed to
the mock pool -- they cannot prove what actually lands in a real jsonb
column. This test writes via the real ``ButlerLogger.log()`` code path
against a migrated-shape Postgres table and reads the row back directly.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.core.butler_logging import ButlerLogger, create_butler_logger

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def test_butler_log_metadata_roundtrips_as_dict_not_double_encoded_string(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        # Mirrors core_089's butler_logs DDL (schema-qualified in production;
        # unqualified here since ButlerLogger issues unqualified table names
        # and relies on the pool's search_path).
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS butler_logs (
                id         BIGSERIAL PRIMARY KEY,
                ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
                level      VARCHAR NOT NULL
                               CHECK (level IN ('DEBUG','INFO','WARN','ERROR')),
                msg        TEXT NOT NULL,
                source     VARCHAR,
                request_id UUID,
                metadata   JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        bl = ButlerLogger(pool=pool, schema="general")
        await bl.log(
            "INFO",
            "structured log line",
            source="test",
            metadata={"nested": {"a": 1}, "tags": ["alpha", "beta"]},
        )

        row = await pool.fetchrow(
            "SELECT metadata FROM butler_logs WHERE msg = 'structured log line'"
        )
        assert row is not None
        stored_metadata = row["metadata"]
        assert isinstance(stored_metadata, dict), (
            f"metadata arrived as {type(stored_metadata).__name__!r}, not a dict — "
            "the jsonb column was double-encoded into a string."
        )
        assert stored_metadata == {"nested": {"a": 1}, "tags": ["alpha", "beta"]}


async def test_migrated_owning_logs_preserve_roles_decoys_and_rollback(
    postgres_container, caplog
) -> None:
    """REQ-core-telemetry-016: real forward migration, writer and selected API.

    This node must execute on hosted PostgreSQL; collection or recorder tests
    never substitute for its role/default-ACL/sequence/commit evidence.
    """
    import asyncio
    import uuid
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from urllib.parse import unquote, urlparse

    import asyncpg
    import httpx
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from alembic import command
    from butlers.api.db import DatabaseManager
    from butlers.api.routers.butler_logs import _get_db_manager
    from butlers.db import Database
    from butlers.migrations import _build_alembic_config
    from butlers.testing.migration import create_migrated_test_db, migration_db_name
    from tests.api.auth_helpers import create_authenticated_domain_app

    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core"],
        {"core": "concierge"},
        {"core": "core_265"},
    )
    config = _build_alembic_config(db_url, target_schema="concierge")
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('concierge.butler_logs')")).scalar() is None
            )
        await asyncio.to_thread(command.upgrade, config, "core_266")
        parsed = urlparse(db_url)
        kwargs = {
            "host": parsed.hostname,
            "port": parsed.port,
            "user": unquote(parsed.username),
            "password": unquote(parsed.password),
        }
        database = Database(
            db_name=parsed.path.lstrip("/"),
            schema="concierge",
            role="butler_concierge_rw",
            min_pool_size=1,
            max_pool_size=1,
            strict_role_enforcement=True,
            **kwargs,
        )
        observed_setup = []
        original_setup = database._setup_connection

        async def observe_original_setup(conn):
            # Observe before delegating to the unchanged source-owned setup.
            # Reused backend + connecting principal proves release RESET ALL.
            observed_setup.append(await conn.fetchrow("SELECT pg_backend_pid(), current_user"))
            await original_setup(conn)

        database._setup_connection = observe_original_setup
        pool = await database.connect()
        manager = DatabaseManager(**kwargs, min_pool_size=1, max_pool_size=1)
        try:
            async with pool.acquire() as conn:
                assert await conn.fetchval("SELECT current_user") == "butler_concierge_rw"
                assert (
                    await conn.fetchval(
                        "SELECT has_sequence_privilege(current_user, "
                        "pg_get_serial_sequence('concierge.butler_logs','id'),'USAGE')"
                    )
                    is True
                )
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await conn.fetchval("SELECT count(*) FROM general.butler_logs")
            async with pool.acquire() as conn:
                assert await conn.fetchval("SELECT current_user") == "butler_concierge_rw"
            assert observed_setup[0][0] == observed_setup[1][0]
            assert observed_setup[0][1] == observed_setup[1][1] == unquote(parsed.username)
            request_id = uuid.uuid4()
            owned = create_butler_logger(pool, "concierge", owning_schema="concierge")
            async with pool.acquire() as wrong_path_connection:
                await wrong_path_connection.execute("SET search_path TO public")

                @asynccontextmanager
                async def original_acquisition():
                    yield wrong_path_connection

                # The actual acquired runtime connection remains role-bound.
                # A misleading search_path must not redirect this real write.
                await create_butler_logger(
                    SimpleNamespace(acquire=original_acquisition),
                    "concierge",
                    owning_schema="concierge",
                ).log(
                    "info",
                    "owned migrated sentinel",
                    request_id=request_id,
                    metadata={"nested": {"value": None}, "tags": ["synthetic"]},
                )
            # A distinct acquisition reads the actual committed producer row.
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, request_id, metadata FROM concierge.butler_logs "
                    "WHERE request_id=$1",
                    request_id,
                )
                assert row is not None and row["request_id"] == request_id
                assert row["metadata"] == {"nested": {"value": None}, "tags": ["synthetic"]}
            with engine.begin() as conn:
                creating_owner = conn.execute(
                    text(
                        "SELECT pg_get_userbyid(relowner) FROM pg_class "
                        "WHERE oid='concierge.butler_logs'::regclass"
                    )
                ).scalar_one()
                assert creating_owner == unquote(parsed.username)
                # Migration-produced public/sibling stores are decoys, never
                # replacement schemas. The public clone is only a planted fixture.
                conn.execute(
                    text(
                        "CREATE TABLE public.butler_logs (LIKE concierge.butler_logs INCLUDING ALL)"
                    )
                )
                conn.execute(
                    text("INSERT INTO public.butler_logs(level,msg) VALUES ('INFO','public decoy')")
                )
                conn.execute(
                    text(
                        "INSERT INTO general.butler_logs(level,msg) VALUES ('INFO','sibling decoy')"
                    )
                )
            await manager.add_butler(
                "concierge", db_name=parsed.path.lstrip("/"), db_schema="concierge"
            )
            app = create_authenticated_domain_app()
            app.dependency_overrides[_get_db_manager] = lambda: manager
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/butlers/concierge/logs")
                assert response.status_code == 200
                assert [r["msg"] for r in response.json()["lines"]] == ["owned migrated sentinel"]
                denied = await client.get(
                    "/api/butlers/concierge/logs", headers={"X-API-Key": "synthetic-invalid-owner"}
                )
                assert denied.status_code == 401
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE concierge.butler_logs RENAME TO held_own_logs"))
                caplog.clear()
                await owned.log("INFO", "missing own must not write public")
                assert "failed to write log line" in caplog.text
                missing = await client.get("/api/butlers/concierge/logs")
                assert missing.status_code == 503
                with engine.begin() as conn:
                    assert (
                        conn.execute(text("SELECT count(*) FROM public.butler_logs")).scalar_one()
                        == 1
                    )
                    conn.execute(text("ALTER TABLE concierge.held_own_logs RENAME TO butler_logs"))
                restored = await client.get("/api/butlers/concierge/logs")
                assert restored.status_code == 200
                assert restored.json()["lines"][0]["request_id"] == str(request_id)
            # A shared-login acquisition cannot borrow runtime writer authority.
            shared = manager.pool("concierge")
            caplog.clear()
            await create_butler_logger(shared, "concierge", owning_schema="concierge").log(
                "INFO", "unverified role must not write"
            )
            assert "failed to write log line" in caplog.text
            assert await pool.fetchval("SELECT count(*) FROM concierge.butler_logs") == 1
        finally:
            await manager.close()
            await database.close()
        # Downgrade and re-upgrade preserve the same row/id/sequence/index state.
        await asyncio.to_thread(command.downgrade, config, "core_265")
        with engine.connect() as conn:
            before = conn.execute(text("SELECT id,msg FROM concierge.butler_logs")).all()
            sequence_before = conn.execute(
                text("SELECT last_value,is_called FROM concierge.butler_logs_id_seq")
            ).one()
        await asyncio.to_thread(command.upgrade, config, "core_266")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id,msg FROM concierge.butler_logs")).all() == before
            assert (
                conn.execute(
                    text("SELECT last_value,is_called FROM concierge.butler_logs_id_seq")
                ).one()
                == sequence_before
            )
        await asyncio.to_thread(command.downgrade, config, "core_265")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE concierge.butler_logs RENAME COLUMN msg TO wrong_msg"))
        with pytest.raises(DBAPIError, match="Incompatible owning structured-log columns"):
            await asyncio.to_thread(command.upgrade, config, "core_266")
        with engine.begin() as conn:
            assert (
                conn.execute(text("SELECT id,wrong_msg FROM concierge.butler_logs")).all() == before
            )
            conn.execute(text("ALTER TABLE concierge.butler_logs RENAME COLUMN wrong_msg TO msg"))
        await asyncio.to_thread(command.upgrade, config, "core_266")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id,msg FROM concierge.butler_logs")).all() == before
        # The normal chain can target a newly configured non-roster schema;
        # no repeated roster allowlist or public log relation supplies its store.
        fresh_config = _build_alembic_config(db_url, target_schema="hc9_fresh")
        await asyncio.to_thread(command.upgrade, fresh_config, "core_265")
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('hc9_fresh.butler_logs')")).scalar() is None
            )
        first, second = await asyncio.gather(
            asyncio.to_thread(_run_owned_log_repair, db_url, "hc9_fresh"),
            asyncio.to_thread(_run_owned_log_repair, db_url, "hc9_fresh"),
        )
        assert first == second == {"completed": True}
        await asyncio.to_thread(command.upgrade, fresh_config, "core_266")
        # Distinct targets use independent transactions and retain both stores.
        own_result, fresh_result = await asyncio.gather(
            asyncio.to_thread(_run_owned_log_repair, db_url, "concierge"),
            asyncio.to_thread(_run_owned_log_repair, db_url, "hc9_fresh"),
        )
        assert own_result == fresh_result == {"completed": True}
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT count(*) FROM hc9_fresh.butler_logs")).scalar_one() == 0
            )
            assert conn.execute(text("SELECT id,msg FROM concierge.butler_logs")).all() == before
            public_before = conn.execute(text("SELECT id,msg FROM public.butler_logs")).all()
        assert await asyncio.to_thread(_run_owned_log_repair, db_url, None) == {"completed": True}
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT id,msg FROM public.butler_logs")).all() == public_before
            )
    finally:
        engine.dispose()


def _run_owned_log_repair(db_url: str, target: str | None) -> dict:
    """Invoke the exact producer in separate actual Alembic/SQL transactions.

    Alembic's process-global proxies are not thread safe. Credentials travel
    only through stdin, with fixed boolean/code-only output on a failed child.
    No role/grant fixture or alternate migration implementation is installed.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    code = """
import importlib.util, json, sys
from pathlib import Path
from sqlalchemy import create_engine
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from alembic.operations import Operations
from butlers.bootstrap_prerequisite import check_bootstrap_connection
from butlers.migrations import _build_alembic_config
request = json.load(sys.stdin)
config = _build_alembic_config(request['url'], target_schema=request['target'])
script = ScriptDirectory.from_config(config)
path = Path('alembic/versions/core/core_266_owned_butler_logs.py')
spec = importlib.util.spec_from_file_location('owned_log_producer', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
engine = create_engine(request['url'])
try:
    with engine.connect() as connection, EnvironmentContext(config, script) as environment:
        check_bootstrap_connection(connection)
        connection.rollback()
        environment.configure(connection=connection)
        with connection.begin(), Operations.context(environment.get_context()):
            module.upgrade()
    print(json.dumps({'completed': True}))
except Exception as error:
    sqlstate = getattr(getattr(error, 'orig', None), 'pgcode', None)
    print(json.dumps({'completed': False, 'privilege_refusal': sqlstate == '42501',
                      'catalog_refusal': sqlstate == 'P0001'}))
    sys.exit(1)
finally:
    engine.dispose()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps({"url": db_url, "target": target}),
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=60,
    )
    assert result.returncode == 0, result.stdout
    return json.loads(result.stdout)
