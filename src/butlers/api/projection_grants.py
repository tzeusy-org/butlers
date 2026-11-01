"""Owner-minted, route-scoped read grants for ambient glance surfaces.

A projection grant lets one non-owner surface (a wall display, an e-ink panel, a
Home Assistant REST sensor) read exactly one projection route without the owner
key or cookie. Spec: ``openspec/changes/add-ambient-projection-grants``
(REQ-ambient-projections-001..003); owner decision bu-xfhsrz (option B).

Only the SHA-256 digest of a token is stored. The token is returned once, at mint.
Verification is an HMAC-safe digest comparison. An unknown, revoked, or mismatched
grant is indistinguishable to the caller (the auth boundary answers 404).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

AUDIENCE_CLASSES = ("household", "owner_private")
SURFACE_KINDS = ("wall_display", "eink", "home_assistant", "other")
ROUTE_SCOPES = ("glance",)
TOKEN_HEADER = "x-projection-token"
TOKEN_QUERY_PARAM = "token"

_GRANT_ROUTE = re.compile(
    r"^/api/projections/(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/(?P<scope>glance)$"
)


@dataclass(frozen=True)
class ProjectionGrant:
    id: uuid.UUID
    label: str
    surface_kind: str
    audience_class: str
    route_scope: str
    token_digest: str
    expected_fetch_interval: timedelta
    created_at: datetime
    revoked_at: datetime | None = None
    last_fetch_at: datetime | None = None
    last_fetch_status: str | None = None


def grant_route(path: str) -> tuple[uuid.UUID, str] | None:
    """Return ``(projection_id, route_scope)`` for an exact grant-bearing route."""
    match = _GRANT_ROUTE.match(path)
    if match is None:
        return None
    return uuid.UUID(match["id"]), match["scope"]


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_matches(grant: ProjectionGrant, token: str) -> bool:
    return hmac.compare_digest(token_digest(token).encode(), grant.token_digest.encode())


class ProjectionGrantStore(Protocol):
    async def mint(
        self,
        *,
        label: str,
        surface_kind: str,
        audience_class: str,
        route_scope: str,
        expected_fetch_interval: timedelta,
    ) -> tuple[ProjectionGrant, str]: ...

    async def list(self) -> list[ProjectionGrant]: ...

    async def get_active(self, projection_id: uuid.UUID) -> ProjectionGrant | None: ...

    async def revoke(self, projection_id: uuid.UUID) -> ProjectionGrant | None: ...

    async def record_fetch(self, projection_id: uuid.UUID, status: str) -> None: ...


_COLUMNS = (
    "id, label, surface_kind, audience_class, route_scope, token_digest, "
    "expected_fetch_interval, created_at, revoked_at, last_fetch_at, last_fetch_status"
)


def _grant(row: Any) -> ProjectionGrant:
    return ProjectionGrant(**{key: row[key] for key in _COLUMNS.split(", ")})


class PostgresProjectionGrantStore:
    """``public.ambient_projections`` through the dashboard API's owning login."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def mint(
        self,
        *,
        label: str,
        surface_kind: str,
        audience_class: str,
        route_scope: str,
        expected_fetch_interval: timedelta,
    ) -> tuple[ProjectionGrant, str]:
        token = new_token()
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO public.ambient_projections
                (id, label, surface_kind, audience_class, route_scope, token_digest,
                 expected_fetch_interval)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING {_COLUMNS}
            """,
            uuid.uuid4(),
            label,
            surface_kind,
            audience_class,
            route_scope,
            token_digest(token),
            expected_fetch_interval,
        )
        return _grant(row), token

    async def list(self) -> list[ProjectionGrant]:
        rows = await self._pool.fetch(
            f"SELECT {_COLUMNS} FROM public.ambient_projections ORDER BY created_at DESC, id"
        )
        return [_grant(row) for row in rows]

    async def get_active(self, projection_id: uuid.UUID) -> ProjectionGrant | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM public.ambient_projections "
            "WHERE id = $1 AND revoked_at IS NULL",
            projection_id,
        )
        return _grant(row) if row is not None else None

    async def revoke(self, projection_id: uuid.UUID) -> ProjectionGrant | None:
        row = await self._pool.fetchrow(
            f"""
            UPDATE public.ambient_projections SET revoked_at = now()
            WHERE id = $1 AND revoked_at IS NULL
            RETURNING {_COLUMNS}
            """,
            projection_id,
        )
        return _grant(row) if row is not None else None

    async def record_fetch(self, projection_id: uuid.UUID, status: str) -> None:
        await self._pool.execute(
            """
            UPDATE public.ambient_projections
            SET last_fetch_at = now(), last_fetch_status = $2
            WHERE id = $1 AND revoked_at IS NULL
            """,
            projection_id,
            status,
        )


def projection_grant_store(app_state: Any) -> ProjectionGrantStore:
    """Return the app's grant store: an injected one, else the shared-pool store.

    Raises ``KeyError`` or ``RuntimeError`` when the database is not available.
    """
    injected = getattr(app_state, "projection_grant_store", None)
    if injected is not None:
        return injected
    from butlers.api.deps import get_db_manager

    return PostgresProjectionGrantStore(get_db_manager().credential_shared_pool())


async def resolve_grant(
    store: ProjectionGrantStore, projection_id: uuid.UUID, route_scope: str, token: str
) -> ProjectionGrant | None:
    """Return the active grant only if the token and route scope both match."""
    grant = await store.get_active(projection_id)
    if grant is None or not token_matches(grant, token) or grant.route_scope != route_scope:
        return None
    return grant
