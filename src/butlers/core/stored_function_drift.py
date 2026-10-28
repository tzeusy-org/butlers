"""Read-only source/catalog authority diagnostics, never an admission capability.

The source compiler supplies exact current function overloads and object ACLs.
There is no bootstrap parser, source-path environment override or accepted old
body variant. Missing or unreadable state cannot report conformance. Returned
identities come exclusively from source; catalog bodies and dynamic error text
never leave this module. Each request observes the actual held connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from butlers.db_authority.catalog import _CATALOG_OBJECT_SQL, observe_catalog_owned
from butlers.db_authority.grants import serving_catalog_contract

logger = logging.getLogger(__name__)

_FUNCTION_FACTS_SQL = """
WITH expected AS (
 SELECT value FROM pg_catalog.jsonb_array_elements(($1::json)::jsonb->'functions')
), normal_owner AS (
 SELECT pg_catalog.pg_get_userbyid(datdba) AS name FROM pg_catalog.pg_database
 WHERE datname=current_database()
)
SELECT expected.value->>'schema' AS schema_name, expected.value->>'name' AS name,
 expected.value->>'signature' AS signature, p.oid IS NOT NULL AS present,
 p.oid IS NOT NULL AND pg_catalog.pg_get_userbyid(p.proowner)=
   COALESCE(expected.value->>'owner',(SELECT name FROM normal_owner)) AS owner_ok,
 p.oid IS NOT NULL AND p.prosecdef=(expected.value->>'definer')::boolean
   AND COALESCE(p.proconfig,ARRAY[]::text[])=ARRAY(
     SELECT pg_catalog.jsonb_array_elements_text(expected.value->'settings')) AS settings_ok,
 CASE WHEN p.oid IS NOT NULL THEN
   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex')
 END AS digest,
 p.oid IS NOT NULL AND NOT EXISTS(
   SELECT FROM pg_catalog.aclexplode(COALESCE(p.proacl,
     pg_catalog.acldefault('f',p.proowner))) a
   LEFT JOIN pg_catalog.pg_roles role ON role.oid=a.grantee
   WHERE a.grantee<>p.proowner AND (a.is_grantable OR NOT EXISTS(
     SELECT FROM pg_catalog.jsonb_array_elements_text(expected.value->'executors') executor
     WHERE COALESCE(executor,(SELECT name FROM normal_owner))=role.rolname))
 ) AND NOT EXISTS(
   SELECT FROM pg_catalog.jsonb_array_elements_text(expected.value->'executors') executor
   WHERE NOT EXISTS(SELECT FROM pg_catalog.aclexplode(COALESCE(p.proacl,
     pg_catalog.acldefault('f',p.proowner))) a
     JOIN pg_catalog.pg_roles role ON role.oid=a.grantee
     WHERE role.rolname=COALESCE(executor,(SELECT name FROM normal_owner))
       AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
 ) AS acl_ok
FROM expected
LEFT JOIN pg_catalog.pg_namespace n ON n.nspname=expected.value->>'schema'
LEFT JOIN pg_catalog.pg_proc p ON p.pronamespace=n.oid
 AND p.proname=expected.value->>'name'
 AND replace(pg_catalog.oidvectortypes(p.proargtypes),', ', ',')=expected.value->>'signature'
ORDER BY schema_name,name,signature
"""


@dataclass(frozen=True)
class FunctionAuthorityFact:
    function: str
    status: str
    expected_digest: str
    actual_digest: str | None
    owner_ok: bool
    settings_ok: bool
    acl_ok: bool


@dataclass(frozen=True)
class DatabaseAuthorityReport:
    checked_at: datetime
    status: str
    functions: tuple[FunctionAuthorityFact, ...] = ()
    objects_ok: bool | None = None
    check_error: str | None = None

    @property
    def is_available(self) -> bool:
        return self.status in {"conforming", "mismatch"} and self.check_error is None


def compare_catalog_functions(expected, observed) -> tuple[FunctionAuthorityFact, ...]:
    """Require exact population and typed facts; never accept a previous variant."""
    index = {(row["schema"], row["name"], row["signature"]): row for row in expected}
    seen = set()
    facts = []
    for row in observed:
        key = (row["schema_name"], row["name"], row["signature"])
        if key not in index or key in seen:
            raise ValueError("Catalog observation has an unsupported identity")
        seen.add(key)
        source = index[key]
        present = row["present"]
        flags = tuple(row[name] for name in ("owner_ok", "settings_ok", "acl_ok"))
        digest = row["digest"]
        if type(present) is not bool or any(type(flag) is not bool for flag in flags):
            raise ValueError("Catalog observation has untyped facts")
        if digest is not None and (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("Catalog observation has an invalid digest")
        matched = present and all(flags) and digest == source["digest"]
        facts.append(
            FunctionAuthorityFact(
                function=f"{key[0]}.{key[1]}({key[2]})",
                status="conforming" if matched else "missing" if not present else "mismatch",
                expected_digest=source["digest"],
                actual_digest=digest,
                owner_ok=flags[0],
                settings_ok=flags[1],
                acl_ok=flags[2],
            )
        )
    if seen != set(index):
        raise ValueError("Catalog observation is incomplete")
    return tuple(facts)


async def compute_database_authority(pool, *, database: str = "butlers") -> DatabaseAuthorityReport:
    """Finite, read-only diagnostics. A diagnostic is not serving admission proof."""
    checked_at = datetime.now(UTC)
    try:
        expected = serving_catalog_contract(database)
        encoded = json.dumps(expected)
        deadline = asyncio.get_running_loop().time() + 5

        async def observe():
            # Query cancellation occurs INSIDE the acquire context. Its full
            # release runs in the retained child, outside the query deadline.
            async with pool.acquire(timeout=5) as connection:
                async with asyncio.timeout_at(deadline):
                    objects_ok = await connection.fetchval(_CATALOG_OBJECT_SQL, encoded)
                    if type(objects_ok) is not bool:
                        raise ValueError("Catalog predicate is unavailable")
                    observed = await connection.fetch(_FUNCTION_FACTS_SQL, encoded)
                    functions = compare_catalog_functions(expected["functions"], observed)
                return objects_ok, functions

        objects_ok, functions = await observe_catalog_owned(pool, observe)
        status = (
            "conforming"
            if objects_ok and all(fact.status == "conforming" for fact in functions)
            else "mismatch"
        )
        return DatabaseAuthorityReport(checked_at, status, functions, objects_ok)
    except Exception as exc:
        return DatabaseAuthorityReport(checked_at, "unavailable", check_error=type(exc).__name__)


def log_database_authority(report: DatabaseAuthorityReport) -> None:
    """Closed diagnostics only, no body, source path, credential or driver text."""
    if not report.is_available:
        logger.warning("Database authority catalog observation is unavailable")
    elif report.status != "conforming":
        logger.warning("Database authority catalog does not conform; controlled migrator required")
