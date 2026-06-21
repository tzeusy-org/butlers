"""DB-level acceptance test for GET /api/contacts/search (bu-mcz0o9).

Runs the real endpoint SQL against a migrated PostgreSQL (core + memory +
relationship chains → ``public.entities`` + ``relationship.entity_facts`` +
``public.entity_info``) so the deterministic ILIKE matching, the secret-store
exclusion, the person-only filter, and the entity_facts→entity join are all
exercised end to end.

Covers the six acceptance scenarios from the bead (re-pointed onto the live
identity layer: the retired ``public.contact_info`` was dropped in core_115 and
its non-secret identifiers re-homed to ``relationship.entity_facts``; secrets
moved to ``public.entity_info`` with ``secured = true``):
1. canonical_name match
2. non-secret identifier match (surfaces the matched identifier)
3. secret entity_info value is neither searched nor returned (never leaks)
4. no-match → empty
5. blank q → empty
6. person-only (organizations/places excluded), incl. merged-entity exclusion

The mocked-pool unit coverage lives in ``test_contacts_search.py``.
"""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.contacts import _get_db_manager
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

SEARCH_PATH = "/api/contacts/search"
BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + memory + relationship chains (public.entities + entity_facts)."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    # entity_facts + entity_info FK → public.entities; TRUNCATE CASCADE clears all.
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


@pytest.fixture
def search_app(pool: asyncpg.Pool) -> FastAPI:
    """FastAPI app whose contacts router is wired to the real migrated pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool

    application = create_app()
    application.dependency_overrides[_get_db_manager] = lambda: mock_db
    return application


async def _add_entity(pool, name, entity_type="person", aliases=None, metadata=None):
    # NOTE: the pool registers a jsonb codec, so ``metadata`` MUST be a dict —
    # passing a JSON *string* would be encoded as a jsonb scalar string, and
    # ``metadata->>'merged_into'`` would then read NULL (the tombstone filter
    # would silently never fire).
    return str(
        await pool.fetchval(
            """
            INSERT INTO public.entities (canonical_name, entity_type, aliases, metadata)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            name,
            entity_type,
            aliases or [],
            metadata or {},
        )
    )


async def _add_fact(pool, subject, predicate, obj):
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, object_kind, src)
        VALUES ($1, $2, $3, 'literal', 'test')
        """,
        subject,
        predicate,
        obj,
    )


async def _add_secret(pool, entity_id, type_, value):
    await pool.execute(
        """
        INSERT INTO public.entity_info (entity_id, type, value, secured)
        VALUES ($1, $2, $3, true)
        """,
        entity_id,
        type_,
        value,
    )


async def _seed(pool: asyncpg.Pool) -> dict[str, str]:
    """Seed entities + non-secret facts + a secret, return label → entity_id."""
    ids: dict[str, str] = {}

    ids["alice"] = await _add_entity(pool, "Alice Anderson", aliases=["Ali", "Andy"])

    ids["bob"] = await _add_entity(pool, "Bob Brown")
    await _add_fact(pool, ids["bob"], "has-email", "bob.secret@example.com")

    # Person whose ONLY match would be a SECRET (secured) entity_info value.
    ids["carol"] = await _add_entity(pool, "Carol Crane")
    await _add_secret(pool, ids["carol"], "google_oauth_refresh", "topsecret-zzqq-token")

    # Organization matching a name query — must be excluded.
    ids["acme"] = await _add_entity(pool, "Alice Industries", entity_type="organization")

    # Merged person matching by name — must be excluded.
    ids["merged"] = await _add_entity(
        pool,
        "Alice Ghost",
        metadata={"merged_into": "00000000-0000-0000-0000-000000000001"},
    )

    return ids


async def _search(app: FastAPI, q: str) -> list[dict]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(SEARCH_PATH, params={"q": q})
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


# ---------------------------------------------------------------------------
# 1. canonical_name match
# ---------------------------------------------------------------------------


async def test_name_match(search_app, pool):
    ids = await _seed(pool)
    results = await _search(search_app, "alice and")  # case-insensitive substring
    matched = {r["entity_id"] for r in results}
    assert ids["alice"] in matched
    alice = next(r for r in results if r["entity_id"] == ids["alice"])
    assert alice["matched_identifier"] is None


async def test_alias_match(search_app, pool):
    ids = await _seed(pool)
    results = await _search(search_app, "andy")
    assert ids["alice"] in {r["entity_id"] for r in results}


# ---------------------------------------------------------------------------
# 2. non-secret identifier match surfaces the matched identifier
# ---------------------------------------------------------------------------


async def test_non_secret_identifier_match(search_app, pool):
    ids = await _seed(pool)
    results = await _search(search_app, "bob.secret@")
    bob = next(r for r in results if r["entity_id"] == ids["bob"])
    assert bob["canonical_name"] == "Bob Brown"
    assert bob["matched_identifier"] == {"type": "email", "value": "bob.secret@example.com"}


# ---------------------------------------------------------------------------
# 3. secret entity_info neither searched nor returned (value never leaks)
# ---------------------------------------------------------------------------


async def test_secret_value_excluded(search_app, pool):
    ids = await _seed(pool)
    results = await _search(search_app, "topsecret")
    # Carol is only reachable via her secured token → must not be returned.
    assert ids["carol"] not in {r["entity_id"] for r in results}
    # The secret value must never appear anywhere in the response.
    assert "topsecret-zzqq-token" not in str(results)


# ---------------------------------------------------------------------------
# 4. no-match → empty
# ---------------------------------------------------------------------------


async def test_no_match_empty(search_app, pool):
    await _seed(pool)
    assert await _search(search_app, "zzz-no-such-person") == []


# ---------------------------------------------------------------------------
# 5. blank q → empty
# ---------------------------------------------------------------------------


async def test_blank_q_empty(search_app, pool):
    await _seed(pool)
    assert await _search(search_app, "   ") == []


# ---------------------------------------------------------------------------
# 6. person-only: organizations + merged entities excluded
# ---------------------------------------------------------------------------


async def test_person_only_excludes_org_and_merged(search_app, pool):
    ids = await _seed(pool)
    results = await _search(search_app, "alice")
    matched = {r["entity_id"] for r in results}
    assert ids["alice"] in matched
    assert ids["acme"] not in matched  # organization
    assert ids["merged"] not in matched  # merged/tombstoned person
