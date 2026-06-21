"""Tests for GET /api/relationship/entities/queue (curation queue endpoint).

Covers spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Requirement: Entity curation queue" and Amendment 12b (owner-only gate).

Each test hits the FastAPI router via httpx.AsyncClient with a mocked DB pool
so no real Postgres or Docker is required.  Tests are marked ``unit`` to avoid
the Docker-availability guard applied to roster/ integration tests.

Acceptance criteria verified:
- Returns UNION of three buckets (unidentified, duplicate-candidate, stale).
- Each result includes entity_id, canonical_name, bucket, evidence, last_seen.
- Owner-only authz gate (Amendment 12b): 403 when no owner entity.
- NO LLM / embedding imports in the endpoint code path.
- Pagination (limit + offset) with total.
- Section ordering: unidentified → duplicate-candidate → stale.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
_OLD_DATE = _NOW - timedelta(days=400)

QUEUE_PATH = "/api/relationship/entities/queue"
BASE_URL = "http://test"


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_queue_row(
    *,
    entity_id: UUID | None = None,
    canonical_name: str = "Alice Example",
    entity_type: str = "person",
    last_seen: datetime | None = None,
    bucket: str = "stale",
    evidence_json: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for queue rows.

    ``evidence_json`` is a dict (simulating the asyncpg JSONB codec output).
    """
    data = {
        "entity_id": entity_id or uuid4(),
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "last_seen": last_seen,
        "bucket": bucket,
        "evidence_json": evidence_json if evidence_json is not None else {},
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_owner_row() -> MagicMock:
    """Simulate a row returned by the owner-entity check query.

    Must include ``roles`` so that ``_get_owner_roles`` can inspect it.
    The endpoint uses ``_get_owner_roles`` which reads ``row["roles"]`` to
    decide whether to grant access.
    """
    data = {"id": uuid4(), "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _app_with_pool(
    *,
    owner_exists: bool = True,
    total: int = 0,
    fetch_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool for the queue endpoint.

    Call sequence inside the endpoint:
      1. pool.fetchrow → owner entity check (None → 403)
      2. pool.fetchval → count(*) query
      3. pool.fetch    → data rows

    ``owner_exists`` controls whether fetchrow returns an owner row.
    ``total`` is returned by ``pool.fetchval`` (the count query).
    ``fetch_rows`` is returned by ``pool.fetch`` (the data query).
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str = QUEUE_PATH, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Scenario: Empty queue
# ---------------------------------------------------------------------------


class TestEmptyQueue:
    """Empty queue returns 200 with empty items and total=0."""

    async def test_empty_queue_returns_200(self):
        app, _ = _app_with_pool(total=0, fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0


# ---------------------------------------------------------------------------
# Scenario: Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    """QueueResponse has items/total/limit/offset; QueueEntry has required fields."""

    async def test_response_has_required_top_level_fields(self):
        row = _make_queue_row(
            entity_id=uuid4(),
            canonical_name="Bob Smith",
            entity_type="person",
            last_seen=_NOW,
            bucket="stale",
            evidence_json={"last_seen": _NOW.isoformat()},
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

    async def test_queue_entry_has_required_fields(self):
        eid = uuid4()
        row = _make_queue_row(
            entity_id=eid,
            canonical_name="Carol Jones",
            entity_type="person",
            last_seen=None,
            bucket="unidentified",
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["entity_id"] == str(eid)
        assert item["canonical_name"] == "Carol Jones"
        assert item["entity_type"] == "person"
        assert item["bucket"] == "unidentified"
        assert "evidence" in item
        assert "last_seen" in item

    async def test_evidence_is_dict_from_jsonb_codec(self):
        evidence = {"predicate": "has-email", "shared_value": "a@b.com", "peer_entity_ids": []}
        row = _make_queue_row(
            bucket="duplicate-candidate",
            evidence_json=evidence,
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["evidence"] == evidence

    async def test_queue_sql_excludes_archived_and_tombstoned_entities(self):
        """The queue data query must emit ``_active_entity_condition`` (router.py),
        excluding archived/tombstoned/deleted entities via IS NULL / IS DISTINCT
        FROM clauses on metadata->>'archived_at', 'tombstone', 'deleted_at'.
        """
        app, pool = _app_with_pool(total=0, fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        fetch_sql = pool.fetch.call_args[0][0]
        assert "archived" in fetch_sql
        assert "archived_at" in fetch_sql
        assert "tombstone" in fetch_sql
        assert "deleted_at" in fetch_sql


# ---------------------------------------------------------------------------
# Scenario: Unidentified bucket
# ---------------------------------------------------------------------------


class TestUnidentifiedBucket:
    """Entities with metadata->>'unidentified'='true' surface in 'unidentified' bucket."""

    async def test_unidentified_entry_has_correct_bucket(self):
        row = _make_queue_row(bucket="unidentified")
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["bucket"] == "unidentified"
        assert item["evidence"] == {}


# ---------------------------------------------------------------------------
# Scenario: Duplicate-candidate bucket
# ---------------------------------------------------------------------------


class TestDuplicateCandidateBucket:
    """Entities sharing email/phone or with dup metadata surface in 'duplicate-candidate'."""

    async def test_dup_entry_has_correct_bucket(self):
        row = _make_queue_row(bucket="duplicate-candidate")
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["bucket"] == "duplicate-candidate"

    async def test_dup_evidence_with_shared_email(self):
        evidence = {
            "predicate": "has-email",
            "shared_value": "alice@example.com",
            "peer_entity_ids": [str(uuid4())],
        }
        row = _make_queue_row(
            bucket="duplicate-candidate",
            evidence_json=evidence,
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["bucket"] == "duplicate-candidate"
        assert item["evidence"]["predicate"] == "has-email"
        assert item["evidence"]["shared_value"] == "alice@example.com"
        assert len(item["evidence"]["peer_entity_ids"]) == 1

    async def test_dup_evidence_with_shared_phone(self):
        evidence = {
            "predicate": "has-phone",
            "shared_value": "+15551234567",
            "peer_entity_ids": [str(uuid4()), str(uuid4())],
        }
        row = _make_queue_row(
            bucket="duplicate-candidate",
            evidence_json=evidence,
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["evidence"]["predicate"] == "has-phone"
        assert len(item["evidence"]["peer_entity_ids"]) == 2


# ---------------------------------------------------------------------------
# Scenario: Stale bucket
# ---------------------------------------------------------------------------


class TestStaleBucket:
    """Entities with no recent last_seen surface in 'stale' bucket."""

    async def test_stale_entry_has_correct_bucket(self):
        evidence = {"last_seen": _OLD_DATE.isoformat()}
        row = _make_queue_row(
            bucket="stale",
            last_seen=_OLD_DATE,
            evidence_json=evidence,
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["bucket"] == "stale"
        assert item["evidence"]["last_seen"] is not None

    async def test_stale_entry_with_null_last_seen(self):
        """Entity with no facts at all: last_seen=null in evidence."""
        evidence = {"last_seen": None}
        row = _make_queue_row(
            bucket="stale",
            last_seen=None,
            evidence_json=evidence,
        )
        app, _ = _app_with_pool(total=1, fetch_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["bucket"] == "stale"
        assert item["last_seen"] is None


# ---------------------------------------------------------------------------
# Scenario: Owner-only authz gate (Clause 12b)
# ---------------------------------------------------------------------------


class TestOwnerAuthzGate:
    """Clause 12b: endpoint returns 403 when no owner entity is registered."""

    async def test_returns_403_when_no_owner_entity(self):
        app, _ = _app_with_pool(owner_exists=False)
        resp = await _get(app)

        assert resp.status_code == 403
        body = resp.json()
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            assert detail.get("code") == "owner_required"
        else:
            assert "owner_required" in str(detail)

    async def test_returns_200_when_owner_entity_present(self):
        app, _ = _app_with_pool(owner_exists=True, total=0, fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    """limit and offset are respected; total reflects pre-pagination count."""

    async def test_default_limit_is_50(self):
        app, _ = _app_with_pool(total=0, fetch_rows=[])
        resp = await _get(app)

        body = resp.json()
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_custom_limit_echoed(self):
        rows = [_make_queue_row() for _ in range(10)]
        app, _ = _app_with_pool(total=10, fetch_rows=rows)
        resp = await _get(app, limit=10)

        body = resp.json()
        assert body["limit"] == 10
        assert body["total"] == 10

    async def test_custom_offset_echoed(self):
        app, _ = _app_with_pool(total=30, fetch_rows=[])
        resp = await _get(app, offset=20)

        body = resp.json()
        assert body["offset"] == 20
        assert body["total"] == 30

    async def test_limit_above_200_rejected_with_422(self):
        app, _ = _app_with_pool()
        resp = await _get(app, limit=201)
        assert resp.status_code == 422

    async def test_limit_exactly_200_accepted(self):
        app, _ = _app_with_pool(total=0, fetch_rows=[])
        resp = await _get(app, limit=200)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: Section ordering
# ---------------------------------------------------------------------------


class TestSectionOrdering:
    """Items must be ordered: unidentified → duplicate-candidate → stale."""

    async def test_section_order_is_correct(self):
        """Items returned by the mock respect bucket ordering (mirrors SQL ORDER BY)."""
        eid_unid = uuid4()
        eid_dup = uuid4()
        eid_stale = uuid4()

        rows = [
            _make_queue_row(entity_id=eid_unid, canonical_name="Alpha", bucket="unidentified"),
            _make_queue_row(entity_id=eid_dup, canonical_name="Beta", bucket="duplicate-candidate"),
            _make_queue_row(entity_id=eid_stale, canonical_name="Gamma", bucket="stale"),
        ]
        app, _ = _app_with_pool(total=3, fetch_rows=rows)
        resp = await _get(app)

        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 3
        assert items[0]["bucket"] == "unidentified"
        assert items[1]["bucket"] == "duplicate-candidate"
        assert items[2]["bucket"] == "stale"


# ---------------------------------------------------------------------------
# Scenario: Scope filter on relationship.entity_facts
# ---------------------------------------------------------------------------


class TestScopeFilter:
    """relationship.entity_facts queries must use schema prefix for isolation, NOT a scope column."""

    async def test_scope_column_absent_from_data_sql(self):
        """Queue SQL must NOT filter AND scope='relationship' on relationship.entity_facts.

        relationship.entity_facts has no scope column.  Schema isolation is enforced
        via the relationship. prefix (RFC 0006).
        """
        app, pool = _app_with_pool(total=0, fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        fetch_sql = pool.fetch.call_args[0][0]
        # Must use schema-qualified name for isolation
        assert "relationship.entity_facts" in fetch_sql, (
            "Queue SQL must use the schema-qualified name relationship.entity_facts"
        )
        # Must NOT filter on the non-existent scope column
        assert "scope = 'relationship'" not in fetch_sql, (
            "Queue SQL must NOT filter AND scope='relationship'; that column does not exist "
            "on relationship.entity_facts"
        )

    async def test_validity_active_filter_present_in_data_sql(self):
        """All relationship.entity_facts queries MUST include validity='active'."""
        app, pool = _app_with_pool(total=0, fetch_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        fetch_sql = pool.fetch.call_args[0][0]
        assert "validity" in fetch_sql
        assert "active" in fetch_sql


# ---------------------------------------------------------------------------
# Guardrail: NO LLM / embedding imports in the endpoint code path
# ---------------------------------------------------------------------------


class TestNoLLMGuardrail:
    """Guardrail test: the queue endpoint MUST NOT import LLM or embedding modules."""

    def test_queue_endpoint_module_has_no_llm_imports(self):
        """Read the router source and assert no LLM/embedding import lines are present.

        Checks import statements only (lines starting with 'import' or 'from').
        Docstrings and comments are not checked — only executable import lines.
        """
        from pathlib import Path

        router_path = Path(__file__).parent.parent.parent / "roster/relationship/api/router.py"
        assert router_path.exists(), f"router.py not found at {router_path}"

        source = router_path.read_text()

        # Only check actual import lines, not comments or docstrings.
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        import_block = "\n".join(import_lines)

        # Forbidden LLM/embedding import patterns.
        forbidden_import_patterns = [
            "import anthropic",
            "from anthropic",
            "import openai",
            "from openai",
            "sentence_transformers",
            "sklearn",
            "cosine_similarity",
            "openai.Embedding",
            "text-embedding",
        ]
        for pattern in forbidden_import_patterns:
            assert pattern not in import_block, (
                f"Forbidden LLM/embedding import '{pattern}' found in router.py imports. "
                "The queue endpoint MUST use deterministic SQL only (no LLM, no embedding)."
            )
