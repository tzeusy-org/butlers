"""Tests for GET /api/relationship/entities (list + filter + pagination).

Covers:
- Empty result set (200 with empty items)
- Pagination defaults (limit=50, offset=0)
- Custom limit
- limit > 200 rejected with HTTP 422
- entity_type filter
- state=unidentified filter
- state=duplicate-candidate filter
- state=stale filter
- has=contact filter (entities with at least one contact triple)
- Unknown state value rejected with HTTP 400
- Unknown has value rejected with HTTP 400
- Response shape (EntityListResponse with items/total/limit/offset)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
_ENTITY_ID_1 = uuid4()
_ENTITY_ID_2 = uuid4()

BASE_URL = "http://test"
LIST_PATH = "/api/relationship/entities"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity_row(
    *,
    entity_id: UUID | None = None,
    canonical_name: str = "Alice Example",
    entity_type: str = "person",
    aliases: list[str] | None = None,
    roles: list[str] | None = None,
    metadata: dict | None = None,
    tier: int | None = None,
    last_seen: datetime | None = None,
    first_seen: datetime | None = None,
    contact_fact_count: int = 0,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for entity rows."""
    data = {
        "id": entity_id or uuid4(),
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "roles": roles or [],
        "metadata": metadata or {},
        "tier": tier,
        "last_seen": last_seen,
        "first_seen": first_seen,
        "contact_fact_count": contact_fact_count,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_pool(
    *,
    total: int = 0,
    fetch_rows: list | None = None,
    fetchval_side_effect=None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    ``total`` is returned by ``pool.fetchval`` (the count query).
    ``fetch_rows`` is returned by ``pool.fetch`` (the data query).
    ``fetchval_side_effect`` overrides ``pool.fetchval`` entirely when provided.
    """
    mock_pool = AsyncMock()
    if fetchval_side_effect is not None:
        mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    # Override _get_db_manager in the relationship router module.
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str = LIST_PATH, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Scenario: Empty result
# ---------------------------------------------------------------------------


async def test_empty_result_returns_200_with_empty_items():
    app, _ = _app_with_pool(total=0, fetch_rows=[])
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["limit"] == 50
    assert body["offset"] == 0


# ---------------------------------------------------------------------------
# Scenario: Response shape (EntityListResponse)
# ---------------------------------------------------------------------------


async def test_response_shape_contains_required_fields():
    _FIRST_SEEN = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    rows = [
        _make_entity_row(
            entity_id=_ENTITY_ID_1,
            canonical_name="Bob Smith",
            entity_type="person",
            aliases=["Bobby"],
            roles=["owner"],
            tier=5,
            last_seen=_NOW,
            first_seen=_FIRST_SEEN,
            contact_fact_count=2,
        )
    ]
    app, _ = _app_with_pool(total=1, fetch_rows=rows)
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["id"] == str(_ENTITY_ID_1)
    assert item["canonical_name"] == "Bob Smith"
    assert item["entity_type"] == "person"
    assert item["aliases"] == ["Bobby"]
    assert item["roles"] == ["owner"]
    assert item["tier"] == 5
    assert item["last_seen"] is not None
    assert item["first_seen"] is not None
    # first_seen must not be later than last_seen
    assert datetime.fromisoformat(item["first_seen"]) <= datetime.fromisoformat(item["last_seen"])
    assert item["contact_fact_count"] == 2
    assert "created_at" in item
    assert "updated_at" in item


# ---------------------------------------------------------------------------
# Scenario: Pagination defaults
# ---------------------------------------------------------------------------


async def test_pagination_defaults_limit_50_offset_0():
    app, pool = _app_with_pool(total=3, fetch_rows=[_make_entity_row() for _ in range(3)])
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    # Verify offset=0 and limit=50 were passed to the DB fetch
    fetch_call_args = pool.fetch.call_args
    sql_and_args = fetch_call_args[0]
    assert 0 in sql_and_args  # offset
    assert 50 in sql_and_args  # limit


# ---------------------------------------------------------------------------
# Scenario: Custom limit
# ---------------------------------------------------------------------------


async def test_custom_limit_is_respected():
    rows = [_make_entity_row() for _ in range(10)]
    app, pool = _app_with_pool(total=10, fetch_rows=rows)
    resp = await _get(app, limit=10)

    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 10
    assert len(body["items"]) == 10


async def test_custom_offset_is_respected():
    rows = [_make_entity_row() for _ in range(5)]
    app, pool = _app_with_pool(total=20, fetch_rows=rows)
    resp = await _get(app, offset=15)

    assert resp.status_code == 200
    body = resp.json()
    assert body["offset"] == 15
    assert body["total"] == 20


# ---------------------------------------------------------------------------
# Scenario: limit > 200 rejected
# ---------------------------------------------------------------------------


async def test_limit_above_200_rejected_with_422():
    app, _ = _app_with_pool()
    resp = await _get(app, limit=201)
    assert resp.status_code == 422  # FastAPI validates via Query(le=200)


async def test_limit_exactly_200_accepted():
    rows = [_make_entity_row() for _ in range(3)]
    app, _ = _app_with_pool(total=3, fetch_rows=rows)
    resp = await _get(app, limit=200)
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 200


# ---------------------------------------------------------------------------
# Scenario: entity_type filter
# ---------------------------------------------------------------------------


async def test_entity_type_filter_passes_to_query():
    app, pool = _app_with_pool(total=0, fetch_rows=[])
    resp = await _get(app, entity_type="organization")

    assert resp.status_code == 200
    # Verify "organization" was sent as a query arg to the pool. The filter is
    # bound as a text[] array param (``e.entity_type = ANY($n::text[])``), so it
    # appears in the positional args as ``["organization"]``.
    fetch_call = pool.fetch.call_args[0]
    assert ["organization"] in fetch_call


async def test_entity_type_filter_returns_matching_entities():
    row = _make_entity_row(entity_type="organization", canonical_name="ACME Corp")
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app, entity_type="organization")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["entity_type"] == "organization"


# ---------------------------------------------------------------------------
# Scenario: archived/tombstoned entities are excluded from default list
# ---------------------------------------------------------------------------


async def test_default_list_sql_excludes_archived_and_tombstoned_entities():
    app, pool = _app_with_pool(total=0, fetch_rows=[])
    resp = await _get(app)

    assert resp.status_code == 200
    fetch_sql = pool.fetch.call_args[0][0]
    assert "archived" in fetch_sql
    assert "archived_at" in fetch_sql
    assert "tombstone" in fetch_sql
    assert "deleted_at" in fetch_sql


# ---------------------------------------------------------------------------
# Scenario: state=unidentified filter
# ---------------------------------------------------------------------------


async def test_state_unidentified_filter_accepted():
    row = _make_entity_row(metadata={"unidentified": "true"})
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app, state="unidentified")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# Scenario: state=duplicate-candidate filter
# ---------------------------------------------------------------------------


async def test_state_duplicate_candidate_filter_accepted():
    row = _make_entity_row(metadata={"duplicate_candidate": "true"})
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app, state="duplicate-candidate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


async def test_state_duplicate_candidate_sql_uses_live_self_join():
    """SQL for state=duplicate-candidate must use the same live detection as the
    queue rail: metadata flag OR shared has-email/has-phone self-join (not just
    the dead metadata flag alone).

    This test guards against the data-contract break where the filter chip returned
    empty results while the queue rail surfaced duplicate candidates (bu-1l8d2).
    """
    app, pool = _app_with_pool(total=0, fetch_rows=[])
    resp = await _get(app, state="duplicate-candidate")
    assert resp.status_code == 200

    # Both the metadata flag path and the live self-join path must be present
    # in the generated SQL.
    count_sql = pool.fetchval.call_args[0][0]
    fetch_sql = pool.fetch.call_args[0][0]

    for sql in (count_sql, fetch_sql):
        # Metadata flag branch
        assert "duplicate_candidate" in sql, "must still check metadata flag"
        # Live detection: entity_facts self-join on has-email / has-phone
        assert "has-email" in sql, "must detect duplicates via has-email self-join"
        assert "has-phone" in sql, "must detect duplicates via has-phone self-join"
        # Must be an OR combination (both paths), not a replacement
        assert "OR" in sql.upper(), "flag and self-join must be OR-combined"


# ---------------------------------------------------------------------------
# Scenario: state=stale filter
# ---------------------------------------------------------------------------


async def test_state_stale_filter_accepted():
    row = _make_entity_row(last_seen=None)
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app, state="stale")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# Scenario: Unknown state rejected
# ---------------------------------------------------------------------------


async def test_unknown_state_rejected_with_400():
    app, _ = _app_with_pool()
    resp = await _get(app, state="nonexistent")
    assert resp.status_code == 400
    body = resp.json()
    assert "state" in body.get("detail", "").lower() or "unknown" in body.get("detail", "").lower()


# ---------------------------------------------------------------------------
# Scenario: has=contact filter returns only entities with contact triples
# ---------------------------------------------------------------------------


async def test_has_contact_filter_accepted():
    row = _make_entity_row(contact_fact_count=1)
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app, has="contact")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


async def test_has_contact_filter_sql_contains_entity_facts():
    """SQL for has=contact must reference relationship.entity_facts with has-* predicates."""
    app, pool = _app_with_pool(total=0, fetch_rows=[])
    resp = await _get(app, has="contact")
    assert resp.status_code == 200
    # Check fetch SQL contains relationship.entity_facts and has-email
    fetch_call_sql = pool.fetch.call_args[0][0]
    assert "relationship.entity_facts" in fetch_call_sql
    assert "has-email" in fetch_call_sql


async def test_has_contact_filter_returns_empty_when_no_contact_triples():
    app, _ = _app_with_pool(total=0, fetch_rows=[])
    resp = await _get(app, has="contact")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# Scenario: Unknown has value rejected
# ---------------------------------------------------------------------------


async def test_unknown_has_value_rejected_with_400():
    app, _ = _app_with_pool()
    resp = await _get(app, has="nonexistent")
    assert resp.status_code == 400
    body = resp.json()
    assert "has" in body.get("detail", "").lower() or "unknown" in body.get("detail", "").lower()


# ---------------------------------------------------------------------------
# Scenario: Entities with no pinned tier return tier=null
# ---------------------------------------------------------------------------


async def test_entity_without_tier_returns_null_tier():
    row = _make_entity_row(tier=None)
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["tier"] is None


# ---------------------------------------------------------------------------
# Scenario: contact_fact_count is 0 for entities without contact triples
# ---------------------------------------------------------------------------


async def test_entity_contact_fact_count_zero():
    row = _make_entity_row(contact_fact_count=0)
    app, _ = _app_with_pool(total=1, fetch_rows=[row])
    resp = await _get(app)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["contact_fact_count"] == 0


# ---------------------------------------------------------------------------
# Scenario: Multiple entities returned in canonical_name order
# ---------------------------------------------------------------------------


async def test_multiple_entities_returned():
    rows = [
        _make_entity_row(canonical_name="Alice"),
        _make_entity_row(canonical_name="Bob"),
        _make_entity_row(canonical_name="Carol"),
    ]
    app, _ = _app_with_pool(total=3, fetch_rows=rows)
    resp = await _get(app)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["total"] == 3
    names = [item["canonical_name"] for item in body["items"]]
    assert names == ["Alice", "Bob", "Carol"]
