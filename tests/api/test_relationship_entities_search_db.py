"""DB-level regression test for GET /api/relationship/entities/search (bu-dv9qa).

Reproduces the production 500 caused by passing the score constants
(``_SCORE_PREFIX`` = 100, etc.) as *untyped* parameters into bare
``SELECT $N AS score`` UNION columns. Against a real PostgreSQL backend
asyncpg infers ``text`` for an untyped param, so binding the Python ``int``
``100`` raises::

    asyncpg.exceptions.DataError: invalid input for query argument $2: 100
    (an integer is required (got type str))

…which surfaces as an HTTP 500 and takes down the Index toolbar search,
Cmd-K Finder, and the merge-target picker.

The unit tests in ``test_relationship_entities_search.py`` mock ``pool.fetch``
and therefore never bind the score params to a real backend — they cannot
catch this class of bug. This test runs the *actual* endpoint SQL against a
migrated Postgres (via testcontainers/Docker), so it fails against the
un-cast SQL and passes once the score params are cast as ``$N::int AS score``.

Mirrors the real-pool harness in
``test_relationship_queue_dismissed_suppression_db.py``.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

SEARCH_PATH = "/api/relationship/entities/search"
BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + memory + relationship chains (flat public topology)."""
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
    await p.execute("TRUNCATE TABLE relationship.entity_facts CASCADE")
    await p.execute("TRUNCATE TABLE public.entities CASCADE")
    yield p
    await p.close()


@pytest.fixture
def search_app(pool: asyncpg.Pool) -> FastAPI:
    """FastAPI app whose relationship router is wired to the real migrated pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool

    application = create_app()
    for butler_name, router_module in application.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            application.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break
    else:  # pragma: no cover - defensive
        raise AssertionError("relationship router not discovered / not DB-wired")
    return application


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_owner(pool: asyncpg.Pool) -> uuid.UUID:
    """Seed the owner entity so the Amendment 12b owner gate passes."""
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner']) RETURNING id",
    )


async def _make_entity(pool: asyncpg.Pool, name: str) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'person') RETURNING id",
        name,
    )


async def _add_contact_fact(
    pool: asyncpg.Pool, *, subject: uuid.UUID, predicate: str, object_value: str
) -> None:
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity, last_seen)
        VALUES ($1, $2, $3, 'literal', 'test', 'active', $4)
        """,
        subject,
        predicate,
        object_value,
        datetime.now(UTC),
    )


async def _get(app: FastAPI, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(SEARCH_PATH, params=params or None)


# ---------------------------------------------------------------------------
# Regression: real-backend search returns 200 with integer scores
# ---------------------------------------------------------------------------


async def test_search_returns_200_with_int_scores_against_real_backend(search_app, pool):
    """The endpoint SQL must bind the score constants as int (not infer text).

    Without the ``$N::int`` casts, asyncpg raises
    ``DataError: invalid input for query argument $2: 100`` and the endpoint
    500s. With the casts, every branch returns its integer score.
    """
    await _make_owner(pool)
    alice = await _make_entity(pool, "Alice Example")  # prefix (100) + substring (50)
    # contact-fact (70): a has-email fact whose object contains the query.
    await _add_contact_fact(pool, subject=alice, predicate="has-email", object_value="alice@x.io")

    resp = await _get(search_app, q="Alice")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    result = next(r for r in body["results"] if r["entity_id"] == str(alice))
    # Highest-scoring branch wins after dedup: prefix == 100.
    assert result["score"] == 100
    assert isinstance(result["score"], int)
    assert result["match_kind"] == "prefix"


async def test_search_contact_fact_branch_scores_int(search_app, pool):
    """Exercise the contact-fact (70) score branch — the $3 untyped param.

    The contact-fact branch only matches ``has-%`` predicates whose literal
    object contains the query; this asserts that branch also returns an
    integer score against the real backend (not just the $2 prefix branch).
    """
    await _make_owner(pool)
    bob = await _make_entity(pool, "Bob")
    # Contact-fact value match: a has-* fact whose object contains "vendor".
    await _add_contact_fact(
        pool, subject=bob, predicate="has-handle", object_value="vendor-account-42"
    )

    resp = await _get(search_app, q="vendor")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    bob_result = next((r for r in body["results"] if r["entity_id"] == str(bob)), None)
    assert bob_result is not None
    assert bob_result["score"] == 70
    assert isinstance(bob_result["score"], int)
    assert bob_result["match_kind"] == "contact_fact"


async def test_search_predicate_branch_scores_int(search_app, pool):
    """Exercise the predicate-label (30) score branch — the $5 untyped param.

    Searching for a term contained in a fact's predicate label hits branch 4
    (``predicate ILIKE '%q%'``). This asserts that branch returns an integer
    score against the real backend.
    """
    await _make_owner(pool)
    carol = await _make_entity(pool, "Carol")
    # Predicate-label match: searching "purchased" matches "purchased-from".
    await _add_contact_fact(pool, subject=carol, predicate="purchased-from", object_value="acme")

    resp = await _get(search_app, q="purchased")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    carol_result = next((r for r in body["results"] if r["entity_id"] == str(carol)), None)
    assert carol_result is not None
    assert carol_result["score"] == 30
    assert isinstance(carol_result["score"], int)
    assert carol_result["match_kind"] == "predicate"
