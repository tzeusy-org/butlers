"""Explicit owner-only history reset for disposable migrated test databases.

Production callers never import this module. Runtime-role tests must exercise
an enabled guard, not use this cleanup as an exception to append-only behavior.
"""

import asyncpg


async def clear_audit_log_for_test(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        async with connection.transaction():
            # Fail before changing the guard if a fixture accidentally supplies
            # a runtime connection. No role switch or privilege grant is made.
            authorized = await connection.fetchval("""
                SELECT c.relowner = r.oid OR r.rolsuper
                FROM pg_class c JOIN pg_roles r ON r.rolname = current_user
                WHERE c.oid = 'public.audit_log'::regclass
            """)
            if authorized is not True:
                raise PermissionError("Disposable audit cleanup requires the real table owner")
            await connection.execute("LOCK TABLE public.audit_log IN ACCESS EXCLUSIVE MODE")
            guarded = await connection.fetchval("""
                SELECT EXISTS (SELECT FROM pg_trigger
                WHERE tgrelid = 'public.audit_log'::regclass
                  AND tgname = 'audit_log_no_truncate' AND NOT tgisinternal)
            """)
            if guarded:
                await connection.execute(
                    "ALTER TABLE public.audit_log DISABLE TRIGGER audit_log_no_truncate"
                )
            await connection.execute("TRUNCATE TABLE public.audit_log")
            if guarded:
                await connection.execute(
                    "ALTER TABLE public.audit_log ENABLE ALWAYS TRIGGER audit_log_no_truncate"
                )
            # All DDL/cleanup is one transaction: failure/cancellation rolls the
            # guard change back instead of leaving an unguarded test database.
