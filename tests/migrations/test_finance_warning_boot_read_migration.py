"""Pure fixed own-boot read: ordinary migrator, real effective roles and catalogs.

These genuine PostgreSQL controls are collected locally, executed by hosted CI.
They do not replace missing facts with a boot registration or grant table access.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.core.utils import generate_uuid7_string
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.integration
_MIGRATION = (
    Path(__file__).parents[2] / "roster/switchboard/migrations/052_finance_warning_boot_read.py"
)
_SIGNATURE = "public.finance_warning_boot_current(uuid,bigint)"
_READ = "SELECT public.finance_warning_boot_current($1::uuid,$2::bigint)"


def _module():
    spec = importlib.util.spec_from_file_location("sw052_boot_read", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["switchboard", "public"])
def boot_reader_db(postgres_container, request):
    schema = request.param
    # Returned credentials are the production-shaped ordinary NOCREATEDB login,
    # not the container control/superuser. Provision and migration are synchronous
    # before an asyncpg connection/loop exists.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": schema},
    ), schema


async def _connect(url, schema, role=None):
    connection = await asyncpg.connect(url, server_settings={"search_path": f"{schema},public"})
    try:
        if role:
            await connection.execute(f'SET ROLE "{role}"')
        return connection
    except BaseException:
        await connection.close(timeout=5)
        raise


async def _install(connection, schema, direction="upgrade"):
    """Run the actual installer statements, preserving ordinary owner identity."""
    module = _module()
    if direction == "upgrade":
        statements = module._statements(schema)
    else:
        from unittest.mock import MagicMock, patch

        op = MagicMock()
        with patch.object(module, "op", op):
            module.downgrade()
        statements = [call.args[0] for call in op.execute.call_args_list]
    async with connection.transaction():
        for statement in statements:
            await connection.execute(statement)


async def _seed(admin, finance, schema):
    await admin.execute(
        f'INSERT INTO "{schema}".butler_registry(name,endpoint_url) '
        "VALUES('finance','http://synthetic.invalid/mcp') ON CONFLICT(name) DO NOTHING"
    )
    boot = UUID(generate_uuid7_string())
    epoch = await finance.fetchval("SELECT public.register_butler_boot('finance',$1)", boot)
    assert epoch == 1
    return boot, epoch


async def _facts(connection, schema):
    return await connection.fetchval(
        f"SELECT jsonb_build_array("
        f'(SELECT jsonb_agg(to_jsonb(c) ORDER BY name) FROM "{schema}".butler_registry_control_plane c),'
        f'(SELECT jsonb_agg(to_jsonb(h) ORDER BY name,boot_epoch) FROM "{schema}".butler_boot_registrations h))::text'
    )


@pytest.mark.asyncio
async def test_ordinary_migrator_installs_pure_role_bound_reader_and_reapplies(boot_reader_db):
    url, schema = boot_reader_db
    admin = finance = other = None
    try:
        admin = await _connect(url, schema)
        finance = await _connect(url, schema, "butler_finance_rw")
        other = await _connect(url, schema, "butler_health_rw")
        profile = await admin.fetchrow(
            "SELECT rolcanlogin,rolsuper,rolcreatedb FROM pg_roles WHERE rolname=current_user"
        )
        assert profile["rolcanlogin"] and not profile["rolsuper"] and not profile["rolcreatedb"]
        assert await admin.fetchval("SELECT pg_has_role(current_user,'butler_finance_rw','SET')")
        owner = await admin.fetchval("SELECT current_user")
        assert not await admin.fetchval(
            "SELECT pg_has_role('butler_finance_rw',$1::name,'SET')", owner
        )
        await _install(admin, schema)
        boot, epoch = await _seed(admin, finance, schema)
        before = await _facts(admin, schema)
        async with finance.transaction(readonly=True):
            assert await finance.fetchval(_READ, boot, epoch) is True
            assert await finance.fetchval(_READ, uuid4(), epoch) is False
            assert await finance.fetchval(_READ, boot, epoch + 1) is False
            assert await finance.fetchval(_READ, None, epoch) is None
        assert await _facts(admin, schema) == before
        catalog = await admin.fetchrow(
            "SELECT p.proowner=current_user::regrole AS owned,p.prosecdef,"
            "p.provolatile::text AS provolatile,p.proconfig,"
            "pg_get_functiondef(p.oid) AS body FROM pg_proc p WHERE p.oid=to_regprocedure($1)",
            _SIGNATURE,
        )
        assert catalog["owned"] and catalog["prosecdef"] and catalog["provolatile"] == "s"
        assert catalog["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert f'"{schema}".butler_registry_control_plane' in catalog["body"]
        assert "register_butler_boot" not in catalog["body"]
        assert await admin.fetchval(
            "SELECT bool_and(x.grantee IN (p.proowner,'butler_finance_rw'::regrole)) "
            "FROM pg_proc p,LATERAL aclexplode(p.proacl) x WHERE p.oid=to_regprocedure($1)",
            _SIGNATURE,
        )
        for table in ("butler_registry_control_plane", "butler_boot_registrations"):
            # Public-layout bootstrap may grant table DML; unchanged RLS must
            # still expose no rows and permit no mutations. Private layout may
            # reject before RLS. Both are actual effective denial, not a new grant.
            try:
                async with finance.transaction():
                    assert await finance.fetch(f'SELECT * FROM "{schema}".{table}') == []
            except asyncpg.InsufficientPrivilegeError:
                pass
            try:
                async with finance.transaction():
                    assert await finance.execute(f'DELETE FROM "{schema}".{table}') == "DELETE 0"
            except asyncpg.InsufficientPrivilegeError:
                pass
            assert await _facts(admin, schema) == before
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await other.fetchval(_READ, boot, epoch)
        # A widened function ACL still cannot turn a different role into Finance.
        acl_probe = admin.transaction()
        await acl_probe.start()
        try:
            await admin.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO butler_health_rw")
            # Separate connection sees only committed ACL; inspect the role guard
            # on the same physical owner connection after a real SET LOCAL ROLE.
            await admin.execute("SET LOCAL ROLE butler_health_rw")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with admin.transaction():
                    await admin.fetchval(_READ, boot, epoch)
        finally:
            await acl_probe.rollback()
        # Temp-shadow names cannot redirect the baked schema or search path.
        await admin.execute("CREATE TEMP TABLE butler_registry_control_plane(name text)")
        await finance.execute("SET search_path=pg_temp,public")
        assert await finance.fetchval(_READ, boot, epoch) is True
        await _install(admin, schema)
        assert await _facts(admin, schema) == before
        await _install(admin, schema, "downgrade")
        assert await admin.fetchval("SELECT to_regprocedure($1)", _SIGNATURE) is None
        assert await _facts(admin, schema) == before
        with pytest.raises(asyncpg.UndefinedFunctionError):
            await finance.fetchval(_READ, boot, epoch)
        await _install(admin, schema)
        assert await finance.fetchval(_READ, boot, epoch) is True
        assert await _facts(admin, schema) == before
    finally:
        results = await asyncio.gather(
            *(conn.close(timeout=5) for conn in (other, finance, admin) if conn is not None),
            return_exceptions=True,
        )
        assert all(result is None for result in results)


@pytest.mark.asyncio
async def test_missing_control_history_and_successor_never_create_or_revive_facts(boot_reader_db):
    url, schema = boot_reader_db
    admin = finance = None
    try:
        admin = await _connect(url, schema)
        finance = await _connect(url, schema, "butler_finance_rw")
        boot, epoch = await _seed(admin, finance, schema)
        await admin.execute(
            f'CREATE TEMP TABLE saved_control AS SELECT * FROM "{schema}".butler_registry_control_plane'
        )
        await admin.execute(
            f'CREATE TEMP TABLE saved_history AS SELECT * FROM "{schema}".butler_boot_registrations'
        )
        for absent in ("control", "history", "both"):
            async with admin.transaction():
                if absent in ("control", "both"):
                    await admin.execute(f'DELETE FROM "{schema}".butler_registry_control_plane')
                if absent in ("history", "both"):
                    await admin.execute(f'DELETE FROM "{schema}".butler_boot_registrations')
            missing = await _facts(admin, schema)
            # Original epoch1 + both facts absent must be UNKNOWN, not a recreated1.
            async with finance.transaction(readonly=True):
                assert await finance.fetchval(_READ, boot, epoch) is None
            assert await _facts(admin, schema) == missing
            async with admin.transaction():
                await admin.execute(
                    f'INSERT INTO "{schema}".butler_registry_control_plane SELECT * FROM saved_control '
                    "ON CONFLICT(name) DO NOTHING"
                )
                await admin.execute(
                    f'INSERT INTO "{schema}".butler_boot_registrations SELECT * FROM saved_history '
                    "ON CONFLICT(name,boot_instance_id) DO NOTHING"
                )
        successor = UUID(generate_uuid7_string())
        new_epoch = await finance.fetchval(
            "SELECT public.register_butler_boot('finance',$1)", successor
        )
        assert new_epoch == epoch + 1
        committed = await _facts(admin, schema)
        async with finance.transaction(readonly=True):
            assert await finance.fetchval(_READ, boot, epoch) is False
            assert await finance.fetchval(_READ, successor, new_epoch) is True
        assert await _facts(admin, schema) == committed
        assert (
            await admin.fetchval(
                f"SELECT boot_instance_id FROM \"{schema}\".butler_registry_control_plane WHERE name='finance'"
            )
            == successor
        )
    finally:
        results = await asyncio.gather(
            *(conn.close(timeout=5) for conn in (finance, admin) if conn is not None),
            return_exceptions=True,
        )
        assert all(result is None for result in results)
