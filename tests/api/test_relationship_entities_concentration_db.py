"""DB-level regression test for GET /api/relationship/entities/concentration (bu-lipzi).

Reproduces the production 500 caused by the predicate-tabs query selecting a
non-existent ``label`` column::

    SELECT predicate, label, description
    FROM relationship.entity_predicate_registry
    WHERE kind = 'relational'
    ORDER BY label ASC

``relationship.entity_predicate_registry`` (migration 014_predicate_registry)
defines ``predicate / kind / object_kind / description`` only — there is NO
``label`` column. Against a real PostgreSQL backend this raises::

    asyncpg.exceptions.UndefinedColumnError: column "label" does not exist

…which surfaces as an HTTP 500 and makes the entire Concentration depth view
unreachable.

The unit tests mock ``pool.fetch`` and never bind this SQL to a real backend,
so they cannot catch this class of bug. This test runs the *actual* endpoint
SQL against a migrated Postgres (via testcontainers/Docker): it fails against
the bad ``label`` column reference and passes once the label is derived from
the predicate slug (``initcap(replace(predicate, '-', ' '))``).

Mirrors the real-pool harness in ``test_relationship_entities_search_db.py``.
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

CONCENTRATION_PATH = "/api/relationship/entities/concentration"
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
def concentration_app(pool: asyncpg.Pool) -> FastAPI:
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


async def _add_relational_fact(
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    obj: uuid.UUID,
    predicate: str,
    weight: int | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity, weight, last_seen)
        VALUES ($1, $2, $3, 'entity', 'test', 'active', $4, $5)
        """,
        subject,
        predicate,
        str(obj),
        weight,
        datetime.now(UTC),
    )


async def _get(app: FastAPI, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(CONCENTRATION_PATH, params=params or None)


# ---------------------------------------------------------------------------
# Regression: real-backend concentration returns 200 with derived labels
# ---------------------------------------------------------------------------


async def test_concentration_returns_200_with_derived_labels(concentration_app, pool):
    """The predicate-tabs query must not reference a non-existent ``label`` column.

    Pre-fix the endpoint 500s with ``UndefinedColumnError: column "label" does
    not exist``. Post-fix every relational predicate tab carries a label
    derived from its slug (``initcap(replace(predicate, '-', ' '))``).
    """
    await _make_owner(pool)
    alice = await _make_entity(pool, "Alice")
    bob = await _make_entity(pool, "Bob")
    # Aggregation groups by the fact SUBJECT, so Alice (subject) is the item.
    await _add_relational_fact(pool, subject=alice, obj=bob, predicate="knows", weight=3)

    resp = await _get(concentration_app, pred="knows")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["predicate"] == "knows"

    # Predicate tabs are present, each with a sensible derived label.
    tabs = body["predicate_tabs"]
    assert len(tabs) >= 1
    for tab in tabs:
        assert tab["label"], f"empty label for predicate {tab['predicate']!r}"
        # Derived label is title-cased and hyphen-free.
        assert "-" not in tab["label"]

    tabs_by_pred = {t["predicate"]: t for t in tabs}
    assert tabs_by_pred["knows"]["label"] == "Knows"
    # Multi-word kebab slugs prettify to space-separated title case.
    assert tabs_by_pred["partner-of"]["label"] == "Partner Of"
    assert tabs_by_pred["co-attended"]["label"] == "Co Attended"

    # The aggregation still works: Alice shows up with her weight.
    item = next(i for i in body["items"] if i["entity_id"] == str(alice))
    assert item["canonical_name"] == "Alice"
    assert item["weight_sum"] == 3
    # Entity-kind target resolves to the object entity's name + id (hyperlink).
    assert item["targets"] == [
        {"name": "Bob", "entity_id": str(bob), "object_kind": "entity"},
    ]


async def test_targets_resolve_entity_and_literal_objects(concentration_app, pool):
    """Targets surface entity-kind (hyperlinked) and literal (plain) objects.

    The ``object::uuid`` cast in the targets subquery is guarded by
    ``object_kind = 'entity'``; this test seeds a *literal* object whose text is
    NOT a valid UUID (``"freelance"``) alongside an entity object to prove the
    CASE guard prevents the cast from ever running on literal rows (otherwise the
    whole query would 500 with ``invalid input syntax for type uuid``).
    """
    await _make_owner(pool)
    alice = await _make_entity(pool, "Alice")
    acme = await _make_entity(pool, "Acme Corp")

    # Entity-kind object: Alice works-at Acme (object is Acme's UUID).
    await _add_relational_fact(pool, subject=alice, obj=acme, predicate="works-at", weight=2)
    # Literal object: free-text "freelance" — NOT a UUID.
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, src, validity, weight, last_seen)
        VALUES ($1, 'works-at', 'freelance', 'literal', 'test', 'active', 1, $2)
        """,
        alice,
        datetime.now(UTC),
    )

    resp = await _get(concentration_app, pred="works-at")

    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["entity_id"] == str(alice))
    # Ordered by name ASC: "Acme Corp" (entity) then "freelance" (literal).
    assert item["targets"] == [
        {"name": "Acme Corp", "entity_id": str(acme), "object_kind": "entity"},
        {"name": "freelance", "entity_id": None, "object_kind": "literal"},
    ]
