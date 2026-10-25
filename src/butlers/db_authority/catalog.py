"""Fresh connecting-login admission on the actual connection being handed out."""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg

from .roles import AuthorityError, PurposeProfile, RuntimeProfile, purpose_profile

# One statement observes one catalog snapshot. Membership traversal deliberately
# includes NOINHERIT and SET-capable edges: RESET ROLE must not regain authority.
_ADMISSION_SQL = """
WITH RECURSIVE reachable(oid) AS (
    SELECT oid FROM pg_catalog.pg_roles WHERE rolname = session_user
    UNION
    SELECT m.roleid FROM pg_catalog.pg_auth_members m
      JOIN reachable r ON m.member = r.oid
), roles AS (
    SELECT p.* FROM pg_catalog.pg_roles p JOIN reachable r ON r.oid = p.oid
)
SELECT
    current_database() = $3 AND current_user = $2
      AND session_user <> ALL($1::text[]) AS identity_ok,
    EXISTS (SELECT FROM roles WHERE rolname = session_user)
      AND NOT EXISTS (SELECT FROM roles
          WHERE rolname <> session_user AND rolname <> ALL($1::text[])) AS role_set_ok,
    NOT EXISTS (SELECT FROM roles WHERE rolsuper OR rolcreaterole OR rolcreatedb
        OR rolreplication OR rolbypassrls)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_auth_members m
          JOIN reachable r ON r.oid = m.member WHERE m.admin_option) AS attributes_ok,
    NOT EXISTS (SELECT FROM pg_catalog.pg_database d JOIN reachable r ON r.oid = d.datdba)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_namespace n
          JOIN reachable r ON r.oid = n.nspowner)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_class c JOIN reachable r ON r.oid = c.relowner)
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_proc p JOIN reachable r ON r.oid = p.proowner)
      AS ownership_ok,
    NOT EXISTS (SELECT FROM roles r CROSS JOIN pg_catalog.pg_database d
        WHERE COALESCE(pg_catalog.has_database_privilege(r.oid, d.oid, 'CREATE'), true))
      AND NOT EXISTS (SELECT FROM roles r CROSS JOIN pg_catalog.pg_namespace n
        WHERE COALESCE(pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE'), true))
      AS create_ok,
    EXISTS (SELECT FROM roles WHERE rolname = $2) AS target_member
"""
_FACTS = (
    "identity_ok",
    "role_set_ok",
    "attributes_ok",
    "ownership_ok",
    "create_ok",
    "target_member",
)
# The dedicated restore executor alone retains the delivered CREATEDB
# capability and ownership of its fixed scratch database. It still cannot own
# the application database/schema/object or reach any other role. The service's
# existing fixed-command firewall remains a separate mandatory boundary.
_PURPOSE_ADMISSION_SQL = (
    _ADMISSION_SQL.replace(
        "session_user <> ALL($1::text[])",
        "(session_user <> ALL($1::text[]) OR ($4::boolean AND session_user = $2))",
    )
    .replace(
        "rolsuper OR rolcreaterole OR rolcreatedb",
        "rolsuper OR rolcreaterole OR (rolcreatedb AND NOT ($4 AND rolname=$2))",
    )
    .replace(
        "ON r.oid = d.datdba)",
        "ON r.oid = d.datdba WHERE NOT ($4 AND d.datname='butlers_restore_drill' "
        "AND r.oid=session_user::regrole))",
    )
    .replace(
        "WHERE COALESCE(pg_catalog.has_database_privilege(r.oid, d.oid, 'CREATE'), true))",
        "WHERE COALESCE(pg_catalog.has_database_privilege(r.oid, d.oid, 'CREATE'), true) "
        "AND NOT ($4 AND d.datname='butlers_restore_drill' AND r.rolname=$2))",
    )
)


async def assert_runtime_authority(connection: Any, profile: RuntimeProfile) -> None:
    """Refuse unknown/unreadable facts; no cached success or caller-asserted privilege."""
    try:
        row = await connection.fetchrow(
            _ADMISSION_SQL, list(profile.runtime_roles), profile.role, profile.database
        )
        if row is None or any(row[key] is not True for key in _FACTS):
            raise AuthorityError("Runtime database authority is unavailable")
    except AuthorityError:
        raise
    except Exception as exc:
        raise AuthorityError("Runtime database authority is unavailable") from exc


async def setup_purpose(connection: Any, profile: PurposeProfile) -> None:
    """Select and freshly prove a fixed non-daemon capability on each handoff."""
    if profile != purpose_profile(profile.database, profile.purpose):
        raise AuthorityError("Database purpose is not declared")
    try:
        await connection.execute(f'SET ROLE "{profile.role}"')
        row = await connection.fetchrow(
            _PURPOSE_ADMISSION_SQL,
            list(profile.runtime_roles),
            profile.role,
            profile.database,
            profile.direct_login,
        )
        if row is None or any(row[key] is not True for key in _FACTS):
            raise AuthorityError("Database purpose authority is unavailable")
    except AuthorityError:
        raise
    except Exception as exc:
        raise AuthorityError("Database purpose authority is unavailable") from exc


_POOL_STARTUP_SETTLEMENTS: set[asyncio.Task] = set()


async def create_owned_pool(**options: Any) -> asyncpg.Pool:
    """Own native initialization, first handoff and late initializer settlement.

    asyncpg initializes eager holders concurrently. Its gather can return one
    failure while sibling connects are still running. Track those actual driver
    callbacks and terminate late connections before discarding their ownership.
    The registry retains only unfinished unpublished-pool cleanup tasks; their
    completion callback drops the final strong reference.
    """
    connecting: set[asyncio.Task] = set()
    closing = False
    driver_connect = options.pop("connect", asyncpg.connect)
    previous_init = options.pop("init", None)

    async def connect(*args: Any, **kwargs: Any):
        task = asyncio.current_task()
        connecting.add(task)
        try:
            connection = await driver_connect(*args, **kwargs)
            if closing:
                connection.terminate()
                raise AuthorityError("Unpublished database pool was canceled")
            return connection
        finally:
            connecting.discard(task)

    async def initialize(connection: Any) -> None:
        task = asyncio.current_task()
        connecting.add(task)
        try:
            if closing:
                raise AuthorityError("Unpublished database pool was canceled")
            if previous_init is not None:
                await previous_init(connection)
        finally:
            if closing:
                connection.terminate()
            connecting.discard(task)

    candidate = asyncpg.create_pool(**options, connect=connect, init=initialize)
    pool = None
    try:
        async with asyncio.timeout(5):
            pool = await candidate
            async with pool.acquire():
                pass
        return pool
    except BaseException as primary:
        closing = True
        pending = tuple(task for task in connecting if task is not asyncio.current_task())
        for task in pending:
            task.cancel()
        owned = pool if pool is not None else candidate

        def terminate() -> None:
            if isinstance(owned, asyncpg.Pool):
                owned.terminate()

        try:
            terminate()
            if not isinstance(owned, asyncpg.Pool) and pool is not None:
                async with asyncio.timeout(5):
                    await pool.close()
        except BaseException:
            primary.add_note("Unpublished database pool cleanup was incomplete")
        if pending:

            async def settle() -> None:
                try:
                    await asyncio.gather(*pending, return_exceptions=True)
                finally:
                    terminate()

            settlement = asyncio.create_task(settle(), name="database-pool-startup-settlement")
            _POOL_STARTUP_SETTLEMENTS.add(settlement)

            def settled(task: asyncio.Task) -> None:
                _POOL_STARTUP_SETTLEMENTS.discard(task)
                if not task.cancelled():
                    task.exception()

            settlement.add_done_callback(settled)
            primary.add_note("Unpublished database pool initialization has retained cleanup")
        raise


async def create_purpose_pool(profile: PurposeProfile, **options: Any) -> asyncpg.Pool:
    """Select and prove a fixed service purpose before publication/every handoff."""
    if profile != purpose_profile(profile.database, profile.purpose):
        raise AuthorityError("Database purpose is not declared")
    previous_setup = options.pop("setup", None)

    async def setup(connection: Any) -> None:
        if previous_setup is not None:
            await previous_setup(connection)
        await setup_purpose(connection, profile)

    return await create_owned_pool(**options, setup=setup)
