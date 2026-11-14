"""Storage for the dashboard vendor-door routes (bu-jv1moa.40, REQ-vendor-doors-006/007).

The routes use their own login, never the dashboard-auth login: that login is refused by
``_initialize_auth_connection`` for reaching any role but ``dashboard_auth_api``, and sharing one
credential between session authority and the door registry would let either side's compromise
reach the other. This login must be a member of the NOLOGIN capability role
``dashboard_vendor_doors`` and of nothing else; every call runs ``SET LOCAL ROLE`` to it inside a
transaction, so the definer functions' ``current_setting('role')`` origin check holds and the
login itself needs no table privilege.

Writes only ever go through ``public.vendor_door_create/confirm/revoke/reject``. Reads select
from ``public.vendor_doors`` under the capability role's SELECT grant.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.db import database_name_from_env, db_params_from_env

logger = logging.getLogger(__name__)

CAPABILITY_ROLE = "dashboard_vendor_doors"
USER_ENV = "DASHBOARD_VENDOR_DOORS_DB_USER"
PASSWORD_ENV = "DASHBOARD_VENDOR_DOORS_DB_PASSWORD"

_SAFE_LOGIN_SQL = """
WITH RECURSIVE reachable(oid) AS (
    SELECT oid FROM pg_roles WHERE rolname = session_user
    UNION
    SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member = r.oid
)
SELECT pg_has_role(session_user, $1, 'MEMBER')
   AND NOT EXISTS (
        SELECT FROM reachable JOIN pg_roles r USING (oid)
        WHERE r.rolname NOT IN (session_user, $1)
           OR r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
           OR r.rolbypassrls OR r.rolreplication
           OR r.oid = (SELECT datdba FROM pg_database WHERE datname = current_database())
   )
"""

_COLUMNS = (
    "id, version, kind, host, template, slot_types, status, origin, tier, sender_verdict, "
    "sender_domain, vendor_entity_id, supersedes_id, expires_at, confirmed_at, revoked_at, "
    "rejected_at, created_at"
)


class VendorDoorStoreUnavailable(RuntimeError):
    """The restricted login is absent or refused; callers answer 503, never an empty list."""


async def _initialize_connection(connection: asyncpg.Connection) -> None:
    """Refuse a login that can reach any authority beyond the capability role."""
    with suppress_instrumentation():
        safe = await connection.fetchval(_SAFE_LOGIN_SQL, CAPABILITY_ROLE)
    if safe is not True:
        raise RuntimeError("vendor-door login holds authority beyond its capability role")


async def create_vendor_door_pool() -> asyncpg.Pool | None:
    """Open the dedicated pool, or return ``None`` (routes then answer 503)."""
    user = os.environ.get(USER_ENV)
    password = os.environ.get(PASSWORD_ENV)
    if not (user and password):
        return None
    try:
        params = db_params_from_env() | {"user": user, "password": password}
        with suppress_instrumentation():
            return await asyncpg.create_pool(
                **params,
                database=database_name_from_env("butlers"),
                min_size=1,
                max_size=2,
                command_timeout=5,
                timeout=5,
                init=_initialize_connection,
            )
    except Exception:
        logger.warning("Vendor-door storage unavailable")
        return None


def _outcome(raw: Any) -> dict[str, Any]:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


class VendorDoorStore:
    def __init__(self, pool: asyncpg.Pool | None) -> None:
        self._pool = pool

    def _require(self) -> asyncpg.Pool:
        if self._pool is None:
            raise VendorDoorStoreUnavailable
        return self._pool

    async def _call(self, sql: str, *args: Any) -> dict[str, Any]:
        pool = self._require()
        try:
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(f"SET LOCAL ROLE {CAPABILITY_ROLE}")
                return _outcome(await conn.fetchval(sql, *args))
        except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
            raise VendorDoorStoreUnavailable from exc

    async def list(
        self,
        *,
        limit: int,
        status: str | None,
        after: tuple[datetime, uuid.UUID] | None,
    ) -> list[dict[str, Any]]:
        pool = self._require()
        clauses: list[str] = []
        args: list[Any] = [limit + 1]
        if status is not None:
            args.append(status)
            clauses.append(f"status = ${len(args)}")
        if after is not None:
            args.extend(after)
            clauses.append(f"(created_at, id) < (${len(args) - 1}, ${len(args)})")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {_COLUMNS} FROM public.vendor_doors {where} "
            "ORDER BY created_at DESC, id DESC LIMIT $1"
        )
        try:
            async with pool.acquire() as conn, conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL ROLE {CAPABILITY_ROLE}")
                rows = await conn.fetch(sql, *args)
        except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
            raise VendorDoorStoreUnavailable from exc
        return [dict(r) for r in rows]

    async def create(
        self,
        *,
        kind: str,
        template: str,
        slot_types: dict[str, Any],
        vendor_entity_id: uuid.UUID | None,
        supersedes: uuid.UUID | None,
    ) -> dict[str, Any]:
        return await self._call(
            "SELECT public.vendor_door_create($1, $2, $3::jsonb, $4, $5)",
            kind,
            template,
            json.dumps(slot_types),
            vendor_entity_id,
            supersedes,
        )

    async def confirm(
        self, door_id: uuid.UUID, expected_version: int, session_hash: str
    ) -> dict[str, Any]:
        return await self._call(
            "SELECT public.vendor_door_confirm($1, $2, $3)",
            door_id,
            expected_version,
            session_hash,
        )

    async def revoke(self, door_id: uuid.UUID, expected_version: int) -> dict[str, Any]:
        return await self._call(
            "SELECT public.vendor_door_revoke($1, $2)", door_id, expected_version
        )

    async def reject(self, door_id: uuid.UUID, expected_version: int) -> dict[str, Any]:
        return await self._call(
            "SELECT public.vendor_door_reject($1, $2)", door_id, expected_version
        )
