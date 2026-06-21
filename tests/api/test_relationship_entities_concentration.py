"""Tests for GET /api/relationship/entities/concentration (weight aggregation endpoint).

Covers spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Requirement: Entity Concentration view" and Amendment 12b (owner-only gate).

Each test hits the FastAPI router via httpx.AsyncClient with a mocked DB pool
so no real Postgres or Docker is required.  Tests are marked ``unit`` to avoid
the Docker-availability guard applied to roster/ integration tests.

Acceptance criteria verified:
- Weight aggregation + rollup (total, top3_share).
- Predicate tabs enumerated from relationship.entity_predicate_registry (kind='relational').
- Owner-only authz gate (Amendment 12b): 403 when no owner entity.
- scope='relationship' AND validity='active' filter on ALL relationship.entity_facts queries.
- ``?pred=`` selects active predicate; default is ``'knows'``.
- Without ``?pred=``, returns predicate_tabs + default rollup.
- Empty result returns items=[], rollup.total=0.
- Provenance fields (src, conf, verified, primary) present on every entry.
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONCENTRATION_PATH = "/api/relationship/entities/concentration"
BASE_URL = "http://test"

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_tab_row(
    *,
    predicate: str = "knows",
    label: str = "Knows",
    description: str | None = None,
    entity_count: int = 0,
) -> MagicMock:
    """Build a MagicMock simulating an asyncpg Record for predicate_registry rows."""
    data = {
        "predicate": predicate,
        "label": label,
        "description": description,
        "entity_count": entity_count,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_agg_row(
    *,
    entity_id: UUID | None = None,
    canonical_name: str = "Alice Example",
    weight_sum: int = 5,
    fact_count: int = 2,
    last_seen: datetime | None = None,
    src: str = "relationship",
    conf: float = 1.0,
    verified: bool = False,
    primary: bool | None = None,
    targets: list[dict] | None = None,
) -> MagicMock:
    """Build a MagicMock simulating an asyncpg Record for aggregation rows.

    ``targets`` mirrors the decoded jsonb array of the endpoint's ``targets``
    subquery (list of ``{name, entity_id, object_kind}`` dicts); defaults to an
    empty list.
    """
    data = {
        "entity_id": entity_id or uuid4(),
        "canonical_name": canonical_name,
        "weight_sum": weight_sum,
        "fact_count": fact_count,
        "last_seen": last_seen,
        "src": src,
        "conf": conf,
        "verified": verified,
        "primary": primary,
        "targets": targets if targets is not None else [],
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
    tab_rows: list | None = None,
    agg_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mocked relationship DB pool.

    Call sequence inside the endpoint:
      1. pool.fetchrow → owner entity check (None → 403)
      2. pool.fetch    → predicate_registry tabs (call #1)
      3. pool.fetch    → aggregation rows (call #2)

    ``owner_exists`` controls whether fetchrow returns an owner row.
    ``tab_rows`` is returned by the first pool.fetch call.
    ``agg_rows`` is returned by the second pool.fetch call.
    """
    default_tabs = [
        _make_tab_row(predicate="knows", label="Knows"),
        _make_tab_row(predicate="family-of", label="Family of"),
    ]
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row() if owner_exists else None)
    # pool.fetch is called twice: tabs, then agg rows.
    mock_pool.fetch = AsyncMock(
        side_effect=[
            tab_rows if tab_rows is not None else default_tabs,
            agg_rows if agg_rows is not None else [],
        ]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str = CONCENTRATION_PATH, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, params=params or None)


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
        app, _ = _app_with_pool(owner_exists=True, agg_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: Empty result
# ---------------------------------------------------------------------------


class TestEmptyResult:
    """Empty concentration returns 200 with items=[], rollup.total=0."""

    async def test_empty_items_list(self):
        app, _ = _app_with_pool(agg_rows=[])
        resp = await _get(app)

        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_empty_rollup_total_is_zero(self):
        app, _ = _app_with_pool(agg_rows=[])
        resp = await _get(app)

        body = resp.json()
        assert body["rollup"]["total"] == 0
        assert body["rollup"]["top3_share"] is None


# ---------------------------------------------------------------------------
# Scenario: Response shape
# ---------------------------------------------------------------------------


class TestResponseShape:
    """Response carries predicate, items, rollup, predicate_tabs, total."""

    async def test_top_level_fields_present(self):
        app, _ = _app_with_pool(agg_rows=[])
        resp = await _get(app)

        body = resp.json()
        assert "predicate" in body
        assert "items" in body
        assert "rollup" in body
        assert "predicate_tabs" in body
        assert "total" in body

    async def test_predicate_tabs_enumerated_from_registry(self):
        tabs = [
            _make_tab_row(predicate="knows", label="Knows"),
            _make_tab_row(predicate="family-of", label="Family of"),
            _make_tab_row(predicate="partner-of", label="Partner of"),
        ]
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=[])
        resp = await _get(app)

        body = resp.json()
        pred_tabs = body["predicate_tabs"]
        assert len(pred_tabs) == 3
        predicates = {t["predicate"] for t in pred_tabs}
        assert predicates == {"knows", "family-of", "partner-of"}


# ---------------------------------------------------------------------------
# Scenario: Single-predicate aggregation
# ---------------------------------------------------------------------------


class TestSinglePredicateAggregation:
    """Aggregation ranks entities by weight_sum DESC."""

    async def test_single_entity_aggregation(self):
        eid = uuid4()
        row = _make_agg_row(entity_id=eid, canonical_name="Bob", weight_sum=7, fact_count=3)
        app, _ = _app_with_pool(agg_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["entity_id"] == str(eid)
        assert item["canonical_name"] == "Bob"
        assert item["weight_sum"] == 7
        assert item["fact_count"] == 3

    async def test_targets_passed_through(self):
        """Each row surfaces its targets (the "where" of the predicate).

        Entity-kind targets carry an ``entity_id`` (for hyperlinking); literal
        targets have ``entity_id=None``.
        """
        org_id = uuid4()
        row = _make_agg_row(
            canonical_name="Alice",
            targets=[
                {"name": "Acme Corp", "entity_id": str(org_id), "object_kind": "entity"},
                {"name": "freelance", "entity_id": None, "object_kind": "literal"},
            ],
        )
        app, _ = _app_with_pool(agg_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        targets = resp.json()["items"][0]["targets"]
        assert targets == [
            {"name": "Acme Corp", "entity_id": str(org_id), "object_kind": "entity"},
            {"name": "freelance", "entity_id": None, "object_kind": "literal"},
        ]

    async def test_targets_default_empty(self):
        """A row with no resolvable objects returns an empty targets list."""
        row = _make_agg_row(canonical_name="Bob")
        app, _ = _app_with_pool(agg_rows=[row])
        resp = await _get(app)

        assert resp.status_code == 200
        assert resp.json()["items"][0]["targets"] == []

    async def test_share_computed_correctly(self):
        """share = weight_sum / total_weight_sum."""
        rows = [
            _make_agg_row(canonical_name="Alice", weight_sum=8),
            _make_agg_row(canonical_name="Bob", weight_sum=2),
        ]
        app, _ = _app_with_pool(agg_rows=rows)
        resp = await _get(app)

        body = resp.json()
        items = body["items"]
        # Total = 10; Alice share = 0.8, Bob share = 0.2.
        total = body["rollup"]["total"]
        assert total == 10
        alice = next(i for i in items if i["canonical_name"] == "Alice")
        bob = next(i for i in items if i["canonical_name"] == "Bob")
        assert abs(alice["share"] - 0.8) < 0.001
        assert abs(bob["share"] - 0.2) < 0.001

    async def test_multiple_entities_ranked_by_weight(self):
        """Items MUST be ordered by weight_sum DESC."""
        rows = [
            # Mock returns them in the expected ORDER BY order (mocked SQL output).
            _make_agg_row(canonical_name="Alice", weight_sum=10),
            _make_agg_row(canonical_name="Bob", weight_sum=5),
            _make_agg_row(canonical_name="Carol", weight_sum=2),
        ]
        app, _ = _app_with_pool(agg_rows=rows)
        resp = await _get(app)

        body = resp.json()
        items = body["items"]
        assert len(items) == 3
        # Response preserves the order returned by SQL (weight_sum DESC).
        assert items[0]["canonical_name"] == "Alice"
        assert items[1]["canonical_name"] == "Bob"
        assert items[2]["canonical_name"] == "Carol"

    async def test_rollup_total_is_sum_of_weight_sums(self):
        rows = [
            _make_agg_row(weight_sum=10),
            _make_agg_row(weight_sum=5),
            _make_agg_row(weight_sum=3),
        ]
        app, _ = _app_with_pool(agg_rows=rows)
        resp = await _get(app)

        assert resp.json()["rollup"]["total"] == 18


# ---------------------------------------------------------------------------
# Scenario: top3Share rollup
# ---------------------------------------------------------------------------


class TestTop3Share:
    """top3_share is top-3 weight_sum / total."""

    async def test_top3_share_with_three_entities(self):
        rows = [
            _make_agg_row(weight_sum=6),
            _make_agg_row(weight_sum=3),
            _make_agg_row(weight_sum=1),
        ]
        app, _ = _app_with_pool(agg_rows=rows)
        resp = await _get(app)

        rollup = resp.json()["rollup"]
        # total=10; top3=10 (all three); top3_share=1.0
        assert rollup["total"] == 10
        assert abs(rollup["top3_share"] - 1.0) < 0.001

    async def test_top3_share_with_more_than_three_entities(self):
        """Only top 3 contribute to top3_share."""
        rows = [
            _make_agg_row(weight_sum=5),
            _make_agg_row(weight_sum=4),
            _make_agg_row(weight_sum=3),
            _make_agg_row(weight_sum=2),
            _make_agg_row(weight_sum=1),
        ]
        app, _ = _app_with_pool(agg_rows=rows)
        resp = await _get(app)

        rollup = resp.json()["rollup"]
        # total=15; top3=12; top3_share=0.8
        assert rollup["total"] == 15
        assert abs(rollup["top3_share"] - 0.8) < 0.001

    async def test_top3_share_with_single_entity(self):
        """Single entity: top3_share = 1.0."""
        rows = [_make_agg_row(weight_sum=7)]
        app, _ = _app_with_pool(agg_rows=rows)
        resp = await _get(app)

        rollup = resp.json()["rollup"]
        assert abs(rollup["top3_share"] - 1.0) < 0.001


# ---------------------------------------------------------------------------
# Scenario: Predicate selection via ?pred=
# ---------------------------------------------------------------------------


class TestPredicateSelection:
    """?pred= selects the active predicate; default is 'knows'."""

    async def test_default_predicate_is_knows(self):
        app, _ = _app_with_pool(agg_rows=[])
        resp = await _get(app)  # no pred param

        body = resp.json()
        assert body["predicate"] == "knows"

    async def test_pred_param_echoed_in_response(self):
        tabs = [
            _make_tab_row(predicate="knows", label="Knows"),
            _make_tab_row(predicate="family-of", label="Family of"),
        ]
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=[])
        resp = await _get(app, pred="family-of")

        assert resp.json()["predicate"] == "family-of"

    async def test_unknown_pred_falls_back_to_knows(self):
        """Unknown predicate falls back to default (knows) if it exists in registry."""
        tabs = [
            _make_tab_row(predicate="knows", label="Knows"),
        ]
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=[])
        resp = await _get(app, pred="nonexistent-predicate")

        assert resp.status_code == 200
        body = resp.json()
        assert body["predicate"] == "knows"


# ---------------------------------------------------------------------------
# Scenario: Smart default predicate (bu-dtfy7)
# ---------------------------------------------------------------------------


class TestSmartDefaultPredicate:
    """When no ?pred= is given, the backend picks the most-populated relational predicate.

    This prevents the first-load empty state when 'knows' has zero rows but other
    relational predicates (works-at, member-of, etc.) have active data.
    """

    async def test_default_picks_most_populated_predicate(self):
        """No ?pred= → response uses the predicate with the highest entity_count."""
        tabs = [
            _make_tab_row(predicate="knows", label="Knows", entity_count=0),
            _make_tab_row(predicate="works-at", label="Works At", entity_count=17),
            _make_tab_row(predicate="member-of", label="Member Of", entity_count=5),
        ]
        agg_rows = [_make_agg_row(canonical_name="Alice", weight_sum=10)]
        app, pool = _app_with_pool(tab_rows=tabs, agg_rows=agg_rows)
        resp = await _get(app)  # no pred param

        assert resp.status_code == 200
        body = resp.json()
        assert body["predicate"] == "works-at"
        # Verify the aggregation SQL was issued for works-at, not knows.
        agg_call = pool.fetch.call_args_list[1]
        sql_args = agg_call[0]
        assert "works-at" in sql_args

    async def test_default_populated_page_has_items(self):
        """Smart default returns populated items, not the empty state."""
        tabs = [
            _make_tab_row(predicate="knows", label="Knows", entity_count=0),
            _make_tab_row(predicate="works-at", label="Works At", entity_count=3),
        ]
        agg_rows = [
            _make_agg_row(canonical_name="Alice", weight_sum=5),
            _make_agg_row(canonical_name="Bob", weight_sum=3),
        ]
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=agg_rows)
        resp = await _get(app)

        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    async def test_default_falls_back_to_knows_when_all_empty(self):
        """When ALL predicates have zero rows, falls back to 'knows' for stable empty state."""
        tabs = [
            _make_tab_row(predicate="knows", label="Knows", entity_count=0),
            _make_tab_row(predicate="works-at", label="Works At", entity_count=0),
        ]
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        body = resp.json()
        assert body["predicate"] == "knows"
        assert body["items"] == []

    async def test_explicit_pred_ignores_smart_default(self):
        """Explicit ?pred= still routes through the standard validation path."""
        tabs = [
            _make_tab_row(predicate="knows", label="Knows", entity_count=0),
            _make_tab_row(predicate="works-at", label="Works At", entity_count=17),
        ]
        # Even though works-at has more data, explicit pred="knows" must be honored.
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=[])
        resp = await _get(app, pred="knows")

        assert resp.status_code == 200
        assert resp.json()["predicate"] == "knows"

    async def test_tab_strip_always_includes_all_predicates(self):
        """predicate_tabs includes all registered predicates regardless of default selection."""
        tabs = [
            _make_tab_row(predicate="knows", label="Knows", entity_count=0),
            _make_tab_row(predicate="works-at", label="Works At", entity_count=17),
            _make_tab_row(predicate="member-of", label="Member Of", entity_count=5),
        ]
        agg_rows = [_make_agg_row(canonical_name="Alice", weight_sum=10)]
        app, _ = _app_with_pool(tab_rows=tabs, agg_rows=agg_rows)
        resp = await _get(app)

        body = resp.json()
        predicates = {t["predicate"] for t in body["predicate_tabs"]}
        assert predicates == {"knows", "works-at", "member-of"}


# ---------------------------------------------------------------------------
# Scenario: Provenance fields on every entry
# ---------------------------------------------------------------------------


class TestProvenanceFields:
    """Every ConcentrationEntry MUST carry src, conf, verified, primary."""

    async def test_provenance_values_correct(self):
        row = _make_agg_row(
            src="relationship",
            conf=0.9,
            verified=True,
            primary=True,
            weight_sum=5,
        )
        app, _ = _app_with_pool(agg_rows=[row])
        resp = await _get(app)

        item = resp.json()["items"][0]
        assert item["src"] == "relationship"
        assert abs(item["conf"] - 0.9) < 0.001
        assert item["verified"] is True
        assert item["primary"] is True

    async def test_null_provenance_fields_explicit(self):
        """Nullable provenance fields are explicit nulls (not omitted)."""
        row = _make_agg_row(primary=None)
        app, _ = _app_with_pool(agg_rows=[row])
        resp = await _get(app)

        item = resp.json()["items"][0]
        assert "primary" in item
        assert item["primary"] is None


# ---------------------------------------------------------------------------
# Scenario: schema-based isolation and validity='active' filter
# ---------------------------------------------------------------------------


class TestScopeFilter:
    """relationship.entity_facts queries must use schema prefix for isolation, NOT a scope column."""

    async def test_scope_column_absent_from_agg_sql(self):
        """Aggregation SQL must NOT include AND scope='relationship' on relationship.entity_facts.

        relationship.entity_facts has no scope column.  Schema isolation is enforced
        via the relationship. prefix (RFC 0006).
        """
        app, pool = _app_with_pool(agg_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        # Aggregation SQL is the second fetch call (index 1).
        agg_sql = pool.fetch.call_args_list[1][0][0]
        assert "relationship.entity_facts" in agg_sql, (
            "Aggregation SQL must use the schema-qualified name relationship.entity_facts"
        )
        assert "scope = 'relationship'" not in agg_sql, (
            "Aggregation SQL must NOT filter AND scope='relationship'; that column does not exist "
            "on relationship.entity_facts"
        )

    async def test_validity_active_filter_present_in_agg_sql(self):
        app, pool = _app_with_pool(agg_rows=[])
        resp = await _get(app)

        assert resp.status_code == 200
        agg_sql = pool.fetch.call_args_list[1][0][0]
        assert "validity = 'active'" in agg_sql or "validity='active'" in agg_sql


# ---------------------------------------------------------------------------
# Scenario: No LLM / embedding guardrail
# ---------------------------------------------------------------------------


class TestNoLLMGuardrail:
    """The concentration endpoint MUST NOT import LLM or embedding modules."""

    def test_concentration_endpoint_has_no_llm_imports(self):
        """Read router.py and assert no LLM/embedding import lines."""
        from pathlib import Path

        router_path = Path(__file__).parent.parent.parent / "roster/relationship/api/router.py"
        assert router_path.exists(), f"router.py not found at {router_path}"

        source = router_path.read_text()
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        import_block = "\n".join(import_lines)

        forbidden_import_patterns = [
            "import anthropic",
            "from anthropic",
            "import openai",
            "from openai",
            "sentence_transformers",
            "sklearn",
            "cosine_similarity",
        ]
        for pattern in forbidden_import_patterns:
            assert pattern not in import_block, (
                f"Forbidden LLM/embedding import '{pattern}' found in router.py. "
                "The concentration endpoint MUST use deterministic SQL only."
            )
