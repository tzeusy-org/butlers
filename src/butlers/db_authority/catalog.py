"""Fresh connecting-login admission on the actual connection being handed out."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
        await assert_object_catalog(connection, profile.database)
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
        await assert_object_catalog(connection, profile.database)
    except AuthorityError:
        raise
    except Exception as exc:
        raise AuthorityError("Database purpose authority is unavailable") from exc


def setup_dbapi_purpose(cursor: Any, profile: PurposeProfile) -> None:
    """Prove the same closed purpose on an owned synchronous utility connection.

    DBAPI uses named placeholders rather than asyncpg's numbered placeholders;
    the catalog predicate and its six strict facts are otherwise identical.
    This grants nothing and does not turn a utility login into a runtime owner.
    """
    if profile != purpose_profile(profile.database, profile.purpose):
        raise AuthorityError("Database purpose is not declared")
    cursor.execute(f'SET ROLE "{profile.role}"')
    sql = _PURPOSE_ADMISSION_SQL
    for number, name in ((1, "roles"), (2, "role"), (3, "database"), (4, "direct")):
        sql = sql.replace(f"${number}", f"%({name})s")
    cursor.execute(
        sql,
        {
            "roles": list(profile.runtime_roles),
            "role": profile.role,
            "database": profile.database,
            "direct": profile.direct_login,
        },
    )
    row = cursor.fetchone()
    if row is None or len(row) != len(_FACTS) or any(value is not True for value in row):
        raise AuthorityError("Database purpose authority is unavailable")
    import json

    from .grants import serving_catalog_contract

    cursor.execute(
        _CATALOG_OBJECT_SQL.replace("%", "%%").replace("$1", "%(catalog)s"),
        {"catalog": json.dumps(serving_catalog_contract(profile.database))},
    )
    object_facts = cursor.fetchone()
    if object_facts is None or len(object_facts) != 1 or object_facts[0] is not True:
        raise AuthorityError("Database object authority is unavailable")


# Native asyncpg Pool is not weak-referenceable. Retain the exact pool only
# while its physical diagnostic/close is unfinished; no permanent ID cache.
@dataclass
class _DiagnosticOwnership:
    pool: Any
    operations: set[asyncio.Task] = field(default_factory=set)
    close_task: asyncio.Task | None = None
    closed_callbacks: list[Callable[[], None]] = field(default_factory=list)
    quarantined: bool = False


_DIAGNOSTIC_OWNERS: dict[int, _DiagnosticOwnership] = {}


def _diagnostic_owner(pool: Any) -> _DiagnosticOwnership:
    owner = _DIAGNOSTIC_OWNERS.get(id(pool))
    if owner is None:
        owner = _DiagnosticOwnership(pool)
        _DIAGNOSTIC_OWNERS[id(pool)] = owner
    return owner


def _drop_settled_diagnostic_owner(owner: _DiagnosticOwnership) -> None:
    if not owner.operations and owner.close_task is None:
        if _DIAGNOSTIC_OWNERS.get(id(owner.pool)) is owner:
            del _DIAGNOSTIC_OWNERS[id(owner.pool)]


async def observe_catalog_owned[Result](
    pool: Any, operation: Callable[[], Awaitable[Result]]
) -> Result:
    """Bound the caller, retain the entire acquire/query/release operation.

    Caller timeout/cancellation never interrupts the physical release. An
    unfinished observation quarantines further diagnostics of this same pool;
    ordinary sibling queries and other pools remain independent. The operation
    itself must bound acquisition/query IO; release remains owned until settled.
    """
    owner = _diagnostic_owner(pool)
    if owner.quarantined or owner.close_task is not None:
        raise AuthorityError("Database diagnostic cleanup is still pending")
    task = asyncio.create_task(operation())
    owner.operations.add(task)

    def settled(done: asyncio.Task) -> None:
        owner.operations.discard(done)
        if not done.cancelled():
            done.exception()  # Observe errors even after the caller has left.
        _drop_settled_diagnostic_owner(owner)

    task.add_done_callback(settled)
    try:
        completed, _ = await asyncio.wait({task}, timeout=5)
        if not completed:
            raise TimeoutError("Database diagnostic deadline expired")
        return task.result()
    finally:
        if not task.done():
            owner.quarantined = True


async def close_after_catalog_diagnostics(
    pool: Any, *, on_closed: Callable[[], None] | None = None, wait_timeout: float | None = 5
) -> None:
    """Join physical diagnostic releases before closing this exact pool.

    One retained close task survives repeated caller cancellation. The default
    five-second wait is an incomplete result, never permission to discard the pool or to
    terminate unrelated shared leases. The owner callback clears its reference
    only after actual close succeeds, including late completion. A retained
    dependent-cleanup owner may join without a second internal deadline while
    independently bounding its own caller.
    """
    owner = _diagnostic_owner(pool)
    if on_closed is not None:
        owner.closed_callbacks.append(on_closed)
    if owner.close_task is None:

        async def close() -> None:
            if owner.operations:
                await asyncio.wait(tuple(owner.operations))
            await pool.close()
            for callback in owner.closed_callbacks:
                callback()

        task = asyncio.create_task(close())
        owner.close_task = task

        def settled(done: asyncio.Task) -> None:
            owner.close_task = None
            owner.closed_callbacks.clear()
            if not done.cancelled():
                done.exception()
            _drop_settled_diagnostic_owner(owner)

        task.add_done_callback(settled)
    task = owner.close_task
    completed, _ = await asyncio.wait({task}, timeout=wait_timeout)
    if not completed:
        raise TimeoutError("Database pool cleanup is still pending")
    task.result()


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


# Uses JSON, not the application's custom JSONB codec: the pre-pool native
# connection has not installed that codec yet. The bound text is source-owned,
# nonsecret authority metadata; dynamic catalog rows can only refuse it.
_CATALOG_OBJECT_SQL = """
WITH expected_schemas AS (
 SELECT value FROM pg_catalog.jsonb_array_elements(($1::json)::jsonb->'schemas')
), expected_relations AS (
 SELECT value FROM pg_catalog.jsonb_array_elements(($1::json)::jsonb->'relations')
), expected_functions AS (
 SELECT value FROM pg_catalog.jsonb_array_elements(($1::json)::jsonb->'functions')
), normal_owner AS (
 SELECT datdba FROM pg_catalog.pg_database WHERE datname=current_database()
), relation_catalog AS (
 SELECT c.*, n.nspname AS schema_name, r.rolname AS owner_name,
        e.value AS expected
 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
 LEFT JOIN expected_relations e ON e.value->>'schema'=n.nspname
                                AND e.value->>'name'=c.relname
 WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
 AND c.relkind IN ('r','p','v','m','S','f')
 AND n.nspname NOT IN ('dashboard_attention','dashboard_stepup')
 AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend d
   WHERE d.classid='pg_catalog.pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
), function_catalog AS (
 SELECT p.*, n.nspname AS schema_name, r.rolname AS owner_name, e.value AS expected
 FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
 LEFT JOIN expected_functions e ON e.value->>'schema'=n.nspname
   AND e.value->>'name'=p.proname
   AND e.value->>'signature'=replace(pg_catalog.oidvectortypes(p.proargtypes),', ', ',')
 WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
 AND n.nspname NOT IN ('dashboard_attention','dashboard_stepup')
 AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend d
   WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')
)
SELECT NOT EXISTS(
 SELECT FROM expected_schemas expected WHERE NOT EXISTS(
    SELECT FROM pg_catalog.pg_namespace n WHERE n.nspname=expected.value->>'name')
) AND NOT EXISTS(
 SELECT FROM pg_catalog.pg_namespace n
 LEFT JOIN expected_schemas expected ON expected.value->>'name'=n.nspname
 WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema'
 AND n.nspname NOT IN ('dashboard_attention','dashboard_stepup')
 AND NOT EXISTS(SELECT FROM pg_catalog.pg_depend d
    WHERE d.classid='pg_catalog.pg_namespace'::regclass AND d.objid=n.oid AND d.deptype='e')
 AND (expected.value IS NULL OR NOT EXISTS(
   SELECT FROM pg_catalog.jsonb_array_elements_text(expected.value->'owners') owner_name
   WHERE pg_catalog.pg_get_userbyid(n.nspowner)=COALESCE(owner_name,
       (SELECT pg_catalog.pg_get_userbyid(datdba) FROM normal_owner)))
 OR EXISTS(
   SELECT FROM pg_catalog.aclexplode(COALESCE(n.nspacl,
       pg_catalog.acldefault('n',n.nspowner))) acl
   LEFT JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
   WHERE acl.grantee<>n.nspowner AND (
     acl.is_grantable OR NOT (
       (acl.privilege_type='USAGE' AND EXISTS(
         SELECT FROM pg_catalog.jsonb_array_elements_text(expected.value->'users') user_name
         WHERE role.rolname=COALESCE(user_name,
           (SELECT pg_catalog.pg_get_userbyid(datdba) FROM normal_owner))))
       OR (acl.privilege_type='CREATE' AND EXISTS(
         SELECT FROM pg_catalog.jsonb_array_elements_text(expected.value->'creators') creator
         WHERE role.rolname=creator))
     )
   )
 )
 OR EXISTS(
   SELECT FROM pg_catalog.jsonb_array_elements_text(expected.value->'creators') creator
   WHERE NOT EXISTS(
     SELECT FROM pg_catalog.aclexplode(COALESCE(n.nspacl,
       pg_catalog.acldefault('n',n.nspowner))) acl
     JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
     WHERE role.rolname=creator AND acl.privilege_type='CREATE' AND NOT acl.is_grantable
   )
 )
 OR EXISTS(SELECT FROM pg_catalog.jsonb_array_elements_text(
      expected.value->'users') user_name
    WHERE COALESCE(user_name,(SELECT pg_catalog.pg_get_userbyid(datdba) FROM normal_owner))
       <>pg_catalog.pg_get_userbyid(n.nspowner)
    AND NOT EXISTS(SELECT FROM pg_catalog.aclexplode(COALESCE(n.nspacl,
       pg_catalog.acldefault('n',n.nspowner))) acl
       JOIN pg_catalog.pg_roles role ON role.oid=acl.grantee
       WHERE role.rolname=COALESCE(user_name,
          (SELECT pg_catalog.pg_get_userbyid(datdba) FROM normal_owner))
         AND acl.privilege_type='USAGE' AND NOT acl.is_grantable)))
) AND NOT EXISTS(
 SELECT FROM expected_relations e WHERE (e.value->>'required')::boolean
 AND NOT EXISTS(SELECT FROM relation_catalog c WHERE c.expected=e.value)
) AND NOT EXISTS(
 SELECT FROM relation_catalog c WHERE
 (c.expected IS NULL AND NOT (
   c.relispartition AND c.relkind='r'
   AND NOT EXISTS(SELECT FROM pg_catalog.aclexplode(COALESCE(c.relacl,
       pg_catalog.acldefault('r',c.relowner))) a WHERE a.grantee<>c.relowner)
   AND 1=(SELECT count(*) FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid)
   AND EXISTS(SELECT FROM pg_catalog.pg_inherits inheritance
     JOIN relation_catalog parent ON parent.oid=inheritance.inhparent
     WHERE inheritance.inhrelid=c.oid AND parent.expected IS NOT NULL
       AND parent.relowner=c.relowner AND parent.relnamespace=c.relnamespace
       AND (parent.schema_name,parent.relname) IN (
        ('public','token_usage_ledger'),('connectors','filtered_events'),
        ('switchboard','message_inbox'),('switchboard','connector_heartbeat_log')))
 )) OR (c.expected IS NOT NULL AND (
   c.owner_name IS DISTINCT FROM COALESCE(c.expected->>'owner',
     (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM normal_owner)))
   OR CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'table'
        WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view'
        WHEN 'S' THEN 'sequence' ELSE 'invalid' END<>c.expected->>'kind'
   OR c.relrowsecurity IS DISTINCT FROM (c.expected->>'row_security')::boolean
   OR c.relforcerowsecurity IS DISTINCT FROM (c.expected->>'forced_rls')::boolean
   OR EXISTS(
    SELECT FROM pg_catalog.aclexplode(COALESCE(c.relacl,
       pg_catalog.acldefault(CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char"
    END,c.relowner))) a
    LEFT JOIN pg_catalog.pg_roles granted ON granted.oid=a.grantee
    WHERE a.grantee<>c.relowner AND (a.is_grantable OR NOT EXISTS(
       SELECT FROM pg_catalog.jsonb_array_elements(c.expected->'permissions') permission
       WHERE COALESCE(permission->>'role',
          (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM
    normal_owner)))=granted.rolname AND permission->>'verb'=a.privilege_type
         AND pg_catalog.jsonb_array_length(permission->'columns')=0)))
   OR EXISTS(
    SELECT FROM pg_catalog.pg_attribute attribute
    CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) a
    LEFT JOIN pg_catalog.pg_roles granted ON granted.oid=a.grantee
    WHERE attribute.attrelid=c.oid AND attribute.attnum>0 AND NOT attribute.attisdropped
      AND a.grantee<>c.relowner AND (a.is_grantable OR NOT EXISTS(
       SELECT FROM pg_catalog.jsonb_array_elements(c.expected->'permissions') permission
       WHERE COALESCE(permission->>'role',
          (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM
    normal_owner)))=granted.rolname AND permission->>'verb'=a.privilege_type
         AND EXISTS(SELECT FROM pg_catalog.jsonb_array_elements_text(permission->'columns')
    column_name
                    WHERE column_name=attribute.attname))))
   OR EXISTS(
    SELECT FROM pg_catalog.jsonb_array_elements(c.expected->'permissions') permission
    CROSS JOIN LATERAL pg_catalog.jsonb_array_elements_text(permission->'columns') column_name
    WHERE NOT EXISTS(
      SELECT FROM pg_catalog.pg_attribute attribute
      CROSS JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) a
      JOIN pg_catalog.pg_roles granted ON granted.oid=a.grantee
      WHERE attribute.attrelid=c.oid AND attribute.attname=column_name
        AND granted.rolname=COALESCE(permission->>'role',
          (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM normal_owner)))
        AND a.privilege_type=permission->>'verb' AND NOT a.is_grantable)
   )
   OR EXISTS(
    SELECT FROM pg_catalog.jsonb_array_elements(c.expected->'permissions') permission
    WHERE NOT EXISTS(
      SELECT FROM pg_catalog.aclexplode(COALESCE(c.relacl,
       pg_catalog.acldefault(CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char"
    END,c.relowner))) a
      JOIN pg_catalog.pg_roles granted ON granted.oid=a.grantee
      WHERE granted.rolname=COALESCE(permission->>'role',
          (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM
    normal_owner))) AND a.privilege_type=permission->>'verb'
    ) AND pg_catalog.jsonb_array_length(permission->'columns')=0
   )
 ))
) AND NOT EXISTS(
 SELECT FROM expected_functions e WHERE NOT EXISTS(
   SELECT FROM function_catalog p WHERE p.expected=e.value)
) AND NOT EXISTS(
 SELECT FROM function_catalog p WHERE p.expected IS NULL
 OR p.owner_name IS DISTINCT FROM COALESCE(p.expected->>'owner',
    (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM normal_owner)))
 OR p.prosecdef IS DISTINCT FROM (p.expected->>'definer')::boolean
 OR (p.prosecdef AND (
    p.expected->>'owner' IS NULL
    OR p.proowner=(SELECT datdba FROM normal_owner)
    OR NOT EXISTS(SELECT FROM pg_catalog.pg_roles owner WHERE owner.oid=p.proowner
      AND NOT owner.rolcanlogin AND NOT owner.rolinherit AND NOT owner.rolsuper
      AND NOT owner.rolcreatedb AND NOT owner.rolcreaterole
      AND NOT owner.rolreplication AND NOT owner.rolbypassrls)
    OR EXISTS(SELECT FROM pg_catalog.pg_auth_members membership
       WHERE membership.roleid=p.proowner OR membership.member=p.proowner)))
 OR COALESCE(p.proconfig,ARRAY[]::text[]) IS DISTINCT FROM
     ARRAY(SELECT pg_catalog.jsonb_array_elements_text(p.expected->'settings'))
 OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
      IS DISTINCT FROM p.expected->>'digest'
 OR EXISTS(SELECT FROM
    pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
   LEFT JOIN pg_catalog.pg_roles granted ON granted.oid=a.grantee
   WHERE a.grantee<>p.proowner AND (a.is_grantable OR NOT EXISTS(
     SELECT FROM pg_catalog.jsonb_array_elements_text(p.expected->'executors') executor
     WHERE COALESCE(executor,
      (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM
    normal_owner)))=granted.rolname)))
 OR EXISTS(SELECT FROM pg_catalog.jsonb_array_elements_text(p.expected->'executors') executor
   WHERE NOT EXISTS(SELECT FROM
    pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
     JOIN pg_catalog.pg_roles granted ON granted.oid=a.grantee
     WHERE granted.rolname=COALESCE(executor,
      (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=(SELECT datdba FROM normal_owner)))
    AND a.privilege_type='EXECUTE' AND NOT a.is_grantable))
) AND NOT EXISTS(
 SELECT FROM pg_catalog.pg_default_acl defaults
 LEFT JOIN pg_catalog.pg_namespace n ON n.oid=defaults.defaclnamespace
 CROSS JOIN LATERAL pg_catalog.aclexplode(defaults.defaclacl) a
 WHERE ((left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema')
     OR (defaults.defaclnamespace=0 AND (defaults.defaclrole=(SELECT datdba FROM normal_owner)
       OR EXISTS(SELECT FROM expected_functions e JOIN pg_catalog.pg_roles role_owner
          ON role_owner.rolname=e.value->>'owner' WHERE role_owner.oid=defaults.defaclrole))))
   AND a.grantee<>defaults.defaclrole
)
"""


# The lexical predicate consumes every non-whitespace character. It does not
# discard parentheses, literal whitespace, casts, operators or qualifications.
# Unsupported spellings refuse instead of weakening a source security fence.
def _security_catalog_sql() -> str:
    from .grants import _SQL_TOKEN_PATTERN

    def tokens(expression):
        return (
            "to_jsonb(ARRAY(SELECT CASE WHEN token[1] ~ '^[A-Za-z_]' "
            "THEN lower(token[1]) ELSE token[1] END FROM "
            f"pg_catalog.regexp_matches({expression}, $security_token${_SQL_TOKEN_PATTERN}"
            "$security_token$, 'g') token))"
        )

    def matches(expression, expected):
        return (
            f"(({expression} IS NULL AND {expected}='null'::jsonb) OR "
            f"({expression} IS NOT NULL AND {expected}<>'null'::jsonb AND "
            f"pg_catalog.regexp_replace({expression}, $security_token${_SQL_TOKEN_PATTERN}"
            "$security_token$, '', 'g') ~ '^[[:space:]]*$' AND "
            f"{tokens(expression)}=to_jsonb(ARRAY(SELECT CASE WHEN value="
            "pg_catalog.quote_literal('__NORMAL_OWNER__') THEN pg_catalog.quote_literal("
            "(SELECT pg_catalog.pg_get_userbyid(datdba) FROM normal_owner)) "
            f"ELSE value END FROM pg_catalog.jsonb_array_elements_text({expected})))))"
        )

    return (
        """
 AND NOT EXISTS (
   SELECT FROM pg_catalog.jsonb_array_elements(($1::json)::jsonb->'policies') expected
   JOIN relation_catalog c ON c.schema_name=expected->>'schema' AND c.relname=expected->>'table'
   WHERE NOT EXISTS(SELECT FROM pg_catalog.pg_policy p
     WHERE p.polrelid=c.oid AND p.polname=expected->>'name')
 ) AND NOT EXISTS (
   SELECT FROM relation_catalog c JOIN pg_catalog.pg_policy p ON p.polrelid=c.oid
   LEFT JOIN pg_catalog.jsonb_array_elements(($1::json)::jsonb->'policies') expected
     ON expected->>'schema'=c.schema_name AND expected->>'table'=c.relname
       AND expected->>'name'=p.polname
   WHERE expected IS NULL OR p.polcmd::text<>expected->>'command'
     OR p.polpermissive IS DISTINCT FROM (expected->>'permissive')::boolean
     OR ARRAY(SELECT CASE WHEN id=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(id)::text END
              FROM unnest(p.polroles) id ORDER BY 1)
          IS DISTINCT FROM ARRAY(SELECT pg_catalog.jsonb_array_elements_text(expected->'roles')
                                 ORDER BY 1)
     OR NOT ("""
        + matches("pg_catalog.pg_get_expr(p.polqual,p.polrelid,false)", "expected->'using'")
        + """)
     OR NOT ("""
        + matches("pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid,false)", "expected->'check'")
        + """)
 ) AND NOT EXISTS (
   SELECT FROM pg_catalog.jsonb_array_elements(($1::json)::jsonb->'triggers') expected
   JOIN relation_catalog c ON c.schema_name=expected->>'schema' AND c.relname=expected->>'table'
   WHERE NOT EXISTS(SELECT FROM pg_catalog.pg_trigger t
     WHERE t.tgrelid=c.oid AND NOT t.tgisinternal AND t.tgname=expected->>'name')
 ) AND NOT EXISTS (
   SELECT FROM relation_catalog c
   JOIN pg_catalog.pg_trigger t ON t.tgrelid=c.oid AND NOT t.tgisinternal
   JOIN pg_catalog.pg_proc fn ON fn.oid=t.tgfoid
   JOIN pg_catalog.pg_namespace fn_schema ON fn_schema.oid=fn.pronamespace
   LEFT JOIN pg_catalog.jsonb_array_elements(($1::json)::jsonb->'triggers') expected
     ON expected->>'schema'=c.schema_name AND expected->>'table'=c.relname
       AND expected->>'name'=t.tgname
   WHERE expected IS NULL OR t.tgtype IS DISTINCT FROM (expected->>'type')::smallint
     OR t.tgenabled::text<>expected->>'enabled'
     OR fn_schema.nspname<>expected->>'function_schema' OR fn.proname<>expected->>'function_name'
     OR pg_catalog.oidvectortypes(fn.proargtypes)<>expected->>'signature'
     OR octet_length(t.tgargs)<>0 OR t.tgoldtable IS NOT NULL OR t.tgnewtable IS NOT NULL
     OR (t.tgconstraint<>0) IS DISTINCT FROM (expected->>'constraint')::boolean
     OR t.tgdeferrable IS DISTINCT FROM (expected->>'deferrable')::boolean
     OR t.tginitdeferred IS DISTINCT FROM (expected->>'deferred')::boolean
     OR ARRAY(SELECT a.attname::text FROM unnest(t.tgattr::smallint[]) WITH ORDINALITY col(id,n)
              JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=col.id
              ORDER BY col.n)
         IS DISTINCT FROM ARRAY(SELECT pg_catalog.jsonb_array_elements_text(expected->'columns'))
     OR NOT ("""
        + matches(
            "CASE WHEN t.tgqual IS NULL THEN NULL ELSE substring("
            "pg_catalog.pg_get_triggerdef(t.oid,false) "
            "FROM ' WHEN (.*) EXECUTE (?:FUNCTION|PROCEDURE) ') END",
            "expected->'when'",
        )
        + """)
 )
"""
    )


_CATALOG_OBJECT_SQL += _security_catalog_sql()


async def assert_object_catalog(connection: Any, database: str) -> None:
    """Recheck source object ownership/body/ACL on the actual held connection."""
    import json

    from .grants import serving_catalog_contract

    try:
        expected = serving_catalog_contract(database)
        if await connection.fetchval(_CATALOG_OBJECT_SQL, json.dumps(expected)) is not True:
            raise AuthorityError("Database object authority is unavailable")
    except AuthorityError:
        raise
    except Exception as exc:
        raise AuthorityError("Database object authority is unavailable") from exc
