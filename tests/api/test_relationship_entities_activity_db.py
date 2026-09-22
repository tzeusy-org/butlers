"""Real-PostgreSQL entity activity source-union regression."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    database = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await database.execute("TRUNCATE TABLE relationship.entity_facts CASCADE")
    await database.execute("TRUNCATE TABLE public.facts CASCADE")
    await database.execute("TRUNCATE TABLE public.entities CASCADE")
    yield database
    await database.close()


def _mcp_manager() -> MagicMock:
    block = MagicMock()
    block.text = json.dumps({"data": [], "count": 0})
    result = MagicMock(content=[block], is_error=False)
    client = AsyncMock()
    client.call_tool = AsyncMock(return_value=result)
    manager = MagicMock()
    manager.get_client = AsyncMock(return_value=client)
    return manager


@pytest.fixture
def activity_app(pool: asyncpg.Pool) -> FastAPI:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app()
    for name, module in app.state.butler_routers:
        if name == "relationship":
            app.dependency_overrides[module._get_db_manager] = lambda: db
            break
    else:  # pragma: no cover
        raise AssertionError("relationship router not discovered")
    app.dependency_overrides[get_mcp_manager] = _mcp_manager
    return app


async def _request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.request(method, path, **kwargs)


async def _seed_entities(pool: asyncpg.Pool) -> tuple[UUID, UUID]:
    owner = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner']) RETURNING id"
    )
    target = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ('Activity Target', 'person') RETURNING id"
    )
    return owner, target


async def test_direct_writes_and_identity_values_round_trip_through_activity(
    pool: asyncpg.Pool,
    activity_app: FastAPI,
) -> None:
    _, entity_id = await _seed_entities(pool)
    base = f"/api/relationship/entities/{entity_id}"
    occurred_at = datetime.now(UTC) - timedelta(hours=1)

    note = await _request(
        activity_app,
        "POST",
        f"{base}/notes",
        json={"content": "Exact note summary", "emotion": "warm"},
    )
    interaction = await _request(
        activity_app,
        "POST",
        f"{base}/interactions",
        json={
            "type": "call",
            "summary": "Exact interaction summary",
            "occurred_at": occurred_at.isoformat(),
            "direction": "outbound",
        },
    )
    gift = await _request(
        activity_app,
        "POST",
        f"{base}/gifts",
        json={"description": "Exact gift summary", "occasion": "birthday"},
    )
    assert (note.status_code, interaction.status_code, gift.status_code) == (201, 201, 201)

    from butlers.tools.relationship.gifts import gift_update_status
    from butlers.tools.relationship.loans import loan_create, loan_settle

    successor = await gift_update_status(pool, UUID(gift.json()["id"]), "purchased")
    loan = await loan_create(
        pool,
        contact_id=entity_id,
        amount=Decimal("12.50"),
        direction="lent",
        description="Exact loan summary",
        currency="SGD",
    )
    loan_successor = await loan_settle(pool, UUID(str(loan["id"])))
    from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

    asserted = await relationship_assert_fact(
        pool,
        entity_id,
        "has-email",
        "exact.identity@example.test",
        src="relationship",
        observed_at=occurred_at,
    )
    assert asserted.fact_id is not None
    identity_id = asserted.fact_id

    response = await _request(activity_app, "GET", f"{base}/activity")
    assert response.status_code == 200
    items = response.json()["items"]
    by_id = {(item["store"], item["id"]): item for item in items}

    assert by_id[("narrative", note.json()["id"])]["summary"] == "Exact note summary"
    assert by_id[("narrative", interaction.json()["id"])]["summary"] == (
        "Exact interaction summary"
    )
    assert by_id[("narrative", str(successor["id"]))]["summary"] == "Exact gift summary"
    assert ("narrative", gift.json()["id"]) not in by_id
    assert by_id[("narrative", str(loan_successor["id"]))]["summary"] == "Exact loan summary"
    assert ("narrative", str(loan["id"])) not in by_id
    assert sum(item["predicate"] == "loan" for item in items) == 1
    assert by_id[("identity", str(identity_id))]["summary"] == "exact.identity@example.test"
    assert all(
        set(item) == {"id", "ts", "kind", "src", "store", "predicate", "episode_id", "summary"}
        for item in items
    )


async def test_activity_preserves_cross_store_collisions_and_bins_before_pagination(
    pool: asyncpg.Pool,
    activity_app: FastAPI,
) -> None:
    _, entity_id = await _seed_entities(pool)
    other_entity = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ('Reverse Activity Source', 'person') RETURNING id"
    )
    collision_id = uuid4()
    now = datetime.now(UTC)
    await pool.execute(
        "INSERT INTO public.facts "
        "(id, subject, predicate, content, validity, scope, entity_id, valid_at) "
        "VALUES ($1, 'collision', 'contact_note', 'Narrative collision', "
        "'active', 'relationship', $2, $3), "
        "(gen_random_uuid(), 'hidden', 'contact_note', 'Retracted', "
        "'retracted', 'relationship', $2, $3)",
        collision_id,
        entity_id,
        now,
    )
    await pool.execute(
        "INSERT INTO relationship.entity_facts "
        "(id, subject, predicate, object, object_kind, src, validity, observed_at) "
        "VALUES ($1, $4, 'member-of', $2::uuid::text, 'entity', "
        "'relationship', 'active', $3), "
        "(gen_random_uuid(), $2, 'works-at', 'Superseded', 'literal', "
        "'relationship', 'superseded', $3)",
        collision_id,
        entity_id,
        now,
        other_entity,
    )

    path = f"/api/relationship/entities/{entity_id}/activity"
    response = await _request(
        activity_app,
        "GET",
        path,
        params={"limit": 1, "offset": 0, "bins": "daily", "window": "2d"},
    )
    body = response.json()

    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert sum(day["count"] for day in body["bins"]) == 2
    all_rows = (await _request(activity_app, "GET", path)).json()["items"]
    assert [(item["store"], item["id"]) for item in all_rows] == [
        ("identity", str(collision_id)),
        ("narrative", str(collision_id)),
    ]
    assert all_rows[0]["summary"] == str(entity_id)


async def test_activity_materializes_more_than_500_rows_per_local_store(
    pool: asyncpg.Pool,
    activity_app: FastAPI,
) -> None:
    _, entity_id = await _seed_entities(pool)
    now = datetime.now(UTC)
    narrative_rows = await pool.fetch(
        "INSERT INTO public.facts "
        "(subject, predicate, content, validity, scope, entity_id, valid_at) "
        "SELECT 'bulk-narrative-' || value, 'contact_note', "
        "'Narrative ' || value, 'active', 'relationship', $1, $2 "
        "FROM generate_series(1, 501) AS value RETURNING id",
        entity_id,
        now,
    )
    identity_rows = await pool.fetch(
        "INSERT INTO relationship.entity_facts "
        "(subject, predicate, object, object_kind, src, validity, observed_at) "
        "SELECT $1, 'bulk-identity', 'Identity ' || value, 'literal', "
        "'relationship', 'active', $2 FROM generate_series(1, 501) AS value RETURNING id",
        entity_id,
        now,
    )

    path = f"/api/relationship/entities/{entity_id}/activity"
    items: list[dict] = []
    totals: set[int] = set()
    bin_total = None
    for offset in range(0, 1200, 200):
        response = await _request(
            activity_app,
            "GET",
            path,
            params={"limit": 200, "offset": offset, "bins": "daily", "window": "2d"},
        )
        body = response.json()
        totals.add(body["total"])
        items.extend(body["items"])
        bin_total = sum(day["count"] for day in body["bins"])

    assert totals == {1002}
    assert len(items) == 1002
    assert bin_total == 1002
    assert {str(row["id"]) for row in identity_rows} <= {item["id"] for item in items}
    assert {str(row["id"]) for row in narrative_rows} <= {item["id"] for item in items}
    assert sum(item["store"] == "identity" for item in items) == 501
    assert sum(item["store"] == "narrative" for item in items) == 501
