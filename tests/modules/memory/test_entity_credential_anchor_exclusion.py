"""Credential-anchor companion entities are excluded from identity-resolution
surfaces (bu-bc9om).

The ``google_account_registry`` and ``steam_account_registry`` specs both require
that the *companion* entity created to anchor an account's credentials
(``roles=['google_account']`` / ``roles=['steam_account']``) MUST NOT surface in
identity resolution: it must not match in ``entity_resolve`` / ``entity_neighbors``
and must not appear in the dashboard entity list / "Unidentified Entities".

These are real-query-path (testcontainers Postgres) tests exercising the actual
production SQL. They assert the steam_account companion is excluded exactly like
the google_account companion, and that the google_account exclusion still holds.

Covers the exclusion sites:
  - ``entity_resolve`` role/exact/prefix tiers  (entities.py)
  - ``entity_neighbors`` final projection        (entities.py)
  - ``GET /api/memory/entities`` list query      (api/routers/memory.py)
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.routers.memory import _get_db_manager
from butlers.modules.memory.tools.entities import entity_neighbors, entity_resolve

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT        NOT NULL,
    entity_type    TEXT        NOT NULL DEFAULT 'person',
    aliases        TEXT[]      NOT NULL DEFAULT '{}',
    roles          TEXT[]      NOT NULL DEFAULT '{}',
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS facts (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id        UUID,
    object_entity_id UUID,
    predicate        TEXT        NOT NULL DEFAULT 'knows',
    content          TEXT,
    confidence       FLOAT       NOT NULL DEFAULT 1.0,
    scope            TEXT        NOT NULL DEFAULT 'global',
    valid_at         TIMESTAMPTZ,
    validity         TEXT        NOT NULL DEFAULT 'active',
    invalid_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _add_entity(
    pool: asyncpg.Pool,
    *,
    canonical_name: str,
    roles: list[str],
    entity_type: str = "person",
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        canonical_name,
        entity_type,
        roles,
    )


async def _add_edge(
    pool: asyncpg.Pool, *, subject: uuid.UUID, obj: uuid.UUID, predicate: str = "knows"
) -> None:
    await pool.execute(
        """
        INSERT INTO facts (entity_id, object_entity_id, predicate, content, validity)
        VALUES ($1, $2, $3, 'edge', 'active')
        """,
        subject,
        obj,
        predicate,
    )


class _SinglePoolDB:
    """DatabaseManager stand-in exposing one real pool under one butler name.

    The butler is deliberately *not* named ``relationship`` so the dashboard
    list endpoint's Dunbar enrichment short-circuits (KeyError → empty map)
    without needing relationship tables provisioned.
    """

    def __init__(self, butler: str, pool: object) -> None:
        self._butler = butler
        self._pool = pool
        self.butler_names = [butler]

    def pool(self, name: str) -> object:
        if name != self._butler:
            raise KeyError(f"No pool for butler: {name}")
        return self._pool


@asynccontextmanager
async def _app_client(db: object):
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.asyncio(loop_scope="session")
async def test_entity_resolve_excludes_credential_anchor_companions(
    provisioned_postgres_pool,
) -> None:
    """entity_resolve must not return google_account or steam_account companions."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=8) as pool:
        await pool.execute(_SCHEMA_SQL)

        name = "Acme Corp"
        normal_id = await _add_entity(pool, canonical_name=name, roles=[], entity_type="other")
        google_id = await _add_entity(
            pool, canonical_name=name, roles=["google_account"], entity_type="other"
        )
        steam_id = await _add_entity(
            pool, canonical_name=name, roles=["steam_account"], entity_type="other"
        )

        results = await entity_resolve(pool, name)
        returned = {r["entity_id"] for r in results}

        assert str(normal_id) in returned, "the real (non-companion) entity must resolve"
        assert str(google_id) not in returned, "google_account companion must be excluded"
        assert str(steam_id) not in returned, "steam_account companion must be excluded"


@pytest.mark.asyncio(loop_scope="session")
async def test_entity_neighbors_excludes_credential_anchor_companions(
    provisioned_postgres_pool,
) -> None:
    """entity_neighbors must not surface google_account or steam_account companions."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=8) as pool:
        await pool.execute(_SCHEMA_SQL)

        hub_id = await _add_entity(pool, canonical_name="Hub", roles=[])
        normal_neighbor = await _add_entity(pool, canonical_name="Friend", roles=[])
        google_neighbor = await _add_entity(
            pool, canonical_name="GMail Anchor", roles=["google_account"], entity_type="other"
        )
        steam_neighbor = await _add_entity(
            pool, canonical_name="Steam Anchor", roles=["steam_account"], entity_type="other"
        )

        for obj in (normal_neighbor, google_neighbor, steam_neighbor):
            await _add_edge(pool, subject=hub_id, obj=obj)

        # direction="outgoing" yields a single non-recursive + single recursive
        # term (a valid WITH RECURSIVE). The default direction="both" emits a
        # multi-UNION-ALL recursive CTE that Postgres rejects — a PRE-EXISTING
        # bug unrelated to this exclusion change (see follow-up bead).
        neighbors = await entity_neighbors(pool, str(hub_id), direction="outgoing")
        neighbor_ids = {n["entity"]["id"] for n in neighbors}

        assert str(normal_neighbor) in neighbor_ids, "ordinary neighbor must be returned"
        assert str(google_neighbor) not in neighbor_ids, "google_account companion excluded"
        assert str(steam_neighbor) not in neighbor_ids, "steam_account companion excluded"


@pytest.mark.asyncio(loop_scope="session")
async def test_dashboard_entity_list_excludes_credential_anchor_companions(
    provisioned_postgres_pool,
) -> None:
    """GET /api/memory/entities must not list google_account or steam_account companions."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=8) as pool:
        await pool.execute(_SCHEMA_SQL)

        normal_id = await _add_entity(pool, canonical_name="Visible Person", roles=[])
        google_id = await _add_entity(
            pool, canonical_name="Google Anchor", roles=["google_account"], entity_type="other"
        )
        steam_id = await _add_entity(
            pool, canonical_name="Steam Anchor", roles=["steam_account"], entity_type="other"
        )

        db = _SinglePoolDB("memory", pool)
        async with _app_client(db) as client:
            resp = await client.get("/api/memory/entities", params={"limit": 200})

        assert resp.status_code == 200, resp.text
        listed = {item["id"] for item in resp.json()["data"]}

        assert str(normal_id) in listed, "ordinary entity must appear in the list"
        assert str(google_id) not in listed, "google_account companion must not be listed"
        assert str(steam_id) not in listed, "steam_account companion must not be listed"
