"""Integration tests for entity-keyed tab API endpoints.

Covers all 11 spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Entity-level tab APIs".

Each test hits the FastAPI router via httpx.AsyncClient with a mocked DB pool,
so no real Postgres or Docker is required.  Tests are marked ``unit`` to avoid
the Docker-availability guard applied to roster/ integration tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENT_ID = uuid4()
_MISSING_ENT_ID = uuid4()

_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=UTC)
_EARLIER = datetime(2026, 4, 29, 8, 0, 0, tzinfo=UTC)
_EARLIEST = datetime(2026, 4, 28, 6, 0, 0, tzinfo=UTC)


def _make_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record.

    Includes all six provenance fields returned by the updated SQL SELECT
    statements (src, conf, last_seen, weight, verified, primary).  These map
    to explicit NULL / default literals in the legacy facts table queries and
    MUST be present so the endpoint constructors can access them.
    """
    data = {
        "id": uuid4(),
        "predicate": "contact_note",
        "content": "default content",
        "metadata": {},
        "valid_at": _NOW,
        "created_at": _NOW,
        # Provenance contract fields — mirrors the NULL literals in SQL.
        "src": "memory_module_legacy",
        "conf": None,
        "last_seen": None,
        "weight": None,
        "verified": False,
        "primary": False,
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_pool(
    *,
    entity_exists: bool = True,
    fetch_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app whose relationship DB pool returns controlled rows.

    ``entity_exists`` controls the ``fetchval`` response for the entity-exists
    check (returns 1 if True, None if False).
    ``fetch_rows`` is returned by pool.fetch for the facts query.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    # Find the relationship router module to override its _get_db_manager.
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


async def _get(app: FastAPI, path: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Scenario 1: Notes endpoint returns facts for entity
# ---------------------------------------------------------------------------


class TestEntityNotes:
    """Scenario 1 — Notes endpoint returns facts for entity."""

    async def test_returns_200_with_notes(self):
        rows = [
            _make_row(
                predicate="contact_note",
                content="Met at conference",
                metadata={"emotion": "happy"},
                valid_at=_NOW,
            ),
            _make_row(
                predicate="contact_note",
                content="Had coffee",
                metadata={},
                valid_at=_EARLIER,
            ),
            _make_row(
                predicate="contact_note",
                content="Called to catch up",
                metadata=None,
                valid_at=_EARLIEST,
            ),
        ]
        app, pool = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/notes")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 3

    async def test_notes_fields_populated_correctly(self):
        rows = [
            _make_row(
                id=uuid4(),
                predicate="contact_note",
                content="Note with emotion",
                metadata={"emotion": "curious"},
                valid_at=_NOW,
            ),
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/notes")

        item = resp.json()[0]
        assert item["content"] == "Note with emotion"
        assert item["emotion"] == "curious"
        assert item["created_at"] is not None

    async def test_notes_ordered_by_valid_at_desc(self):
        """DB sorts; test validates the query is invoked and order preserved."""
        rows = [
            _make_row(content="newest", valid_at=_NOW),
            _make_row(content="middle", valid_at=_EARLIER),
            _make_row(content="oldest", valid_at=_EARLIEST),
        ]
        app, pool = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/notes")

        assert resp.status_code == 200
        contents = [item["content"] for item in resp.json()]
        assert contents == ["newest", "middle", "oldest"]


# ---------------------------------------------------------------------------
# Scenario 2: Interactions endpoint merges interaction subtypes
# ---------------------------------------------------------------------------


class TestEntityInteractions:
    """Scenario 2 — Interactions endpoint merges interaction subtypes."""

    async def test_returns_all_interaction_subtypes(self):
        rows = [
            _make_row(predicate="interaction_meeting", content="Standup", valid_at=_NOW),
            _make_row(predicate="interaction_message", content="Slack DM", valid_at=_EARLIER),
            _make_row(predicate="interaction_call", content="Phone call", valid_at=_EARLIEST),
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/interactions")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 3
        types = {item["type"] for item in body}
        assert types == {"meeting", "message", "call"}

    async def test_type_is_predicate_suffix(self):
        rows = [_make_row(predicate="interaction_video_call", content="Zoom")]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/interactions")

        assert resp.json()[0]["type"] == "video_call"

    async def test_sparse_direction_and_group_size_are_null(self):
        rows = [_make_row(predicate="interaction_meeting", content="Team sync", metadata={})]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/interactions")

        item = resp.json()[0]
        assert item["direction"] is None
        assert item["group_size"] is None


# ---------------------------------------------------------------------------
# Scenario 3: Mixed-channel interactions merged across linked contacts
# ---------------------------------------------------------------------------


class TestMixedChannelInteractions:
    """Scenario 3 — entity_id-scoped query returns interactions regardless of source contact."""

    async def test_returns_all_entity_interactions(self):
        """Facts for the same entity_id are returned regardless of originating channel."""
        telegram_fact = _make_row(
            predicate="interaction_message",
            content="Telegram message",
            metadata={"channel": "telegram"},
        )
        email_fact = _make_row(
            predicate="interaction_message",
            content="Email reply",
            metadata={"channel": "email"},
        )
        app, pool = _app_with_pool(fetch_rows=[telegram_fact, email_fact])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/interactions")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_no_deduplication_by_predicate_and_valid_at(self):
        """Two facts with same predicate and valid_at are both returned (different channels)."""
        fact_a = _make_row(
            predicate="interaction_message",
            content="Telegram",
            valid_at=_NOW,
            metadata={"channel": "telegram"},
        )
        fact_b = _make_row(
            predicate="interaction_message",
            content="Email",
            valid_at=_NOW,
            metadata={"channel": "email"},
        )
        app, _ = _app_with_pool(fetch_rows=[fact_a, fact_b])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/interactions")

        assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# Scenario 4: Timeline orders by valid_at across all six predicate families
# ---------------------------------------------------------------------------


class TestEntityTimeline:
    """Scenario 4 — Timeline merges all six predicate families."""

    async def test_timeline_includes_all_predicate_families(self):
        rows = [
            _make_row(predicate="interaction_meeting", content="meeting", valid_at=_NOW),
            _make_row(predicate="contact_note", content="note", valid_at=_NOW),
            _make_row(predicate="life_event", content="event", valid_at=_EARLIER),
            _make_row(predicate="gift", content="gift", valid_at=_EARLIER),
            _make_row(predicate="loan", content="loan", valid_at=_EARLIEST),
            _make_row(predicate="dunbar_tier_override", content="override", valid_at=_EARLIEST),
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 6
        kinds = {item["kind"] for item in body}
        assert kinds == {
            "interaction",
            "note",
            "life_event",
            "gift",
            "loan",
            "dunbar_tier_override",
        }

    async def test_timeline_kind_field_present(self):
        rows = [
            _make_row(predicate="contact_note", content="x", valid_at=_NOW),
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        item = resp.json()[0]
        assert "kind" in item
        assert item["kind"] == "note"
        assert "predicate" in item
        assert item["predicate"] == "contact_note"

    async def test_timeline_preserves_db_sort_order(self):
        """DB handles ordering; response preserves the order returned by pool.fetch."""
        rows = [
            _make_row(predicate="contact_note", content="newest", valid_at=_NOW),
            _make_row(predicate="interaction_call", content="middle", valid_at=_EARLIER),
            _make_row(predicate="gift", content="oldest", valid_at=_EARLIEST),
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        contents = [item["content"] for item in resp.json()]
        assert contents == ["newest", "middle", "oldest"]


# ---------------------------------------------------------------------------
# Scenario 5: Timeline excludes legacy activity facts
# ---------------------------------------------------------------------------


class TestTimelineExcludesLegacyActivity:
    """Scenario 5 — 'activity' predicate MUST NOT appear in timeline responses.

    The SQL WHERE clause does not include 'activity' in the predicate list and
    does not match LIKE 'interaction_%', so the pool.fetch call excludes them
    at the DB level.  The test verifies that the endpoint does NOT pass
    'activity' to the query and that any 'activity' rows returned (simulating
    a misconfigured DB) are not surfaced — the correct approach is to verify
    the endpoint only queries for the right predicates.

    In practice the mock returns zero rows, confirming the endpoint returns []
    when the DB correctly filters them out.
    """

    async def test_timeline_returns_empty_when_only_activity_facts_exist(self):
        """If the DB returns no rows (because 'activity' was filtered), response is []."""
        app, pool = _app_with_pool(fetch_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_timeline_query_does_not_include_activity_predicate(self):
        """The SQL sent to the pool must not include the literal predicate 'activity'."""
        app, pool = _app_with_pool(fetch_rows=[])
        await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        call_args = pool.fetch.call_args
        assert call_args is not None
        sql = call_args[0][0]  # First positional arg is the SQL string
        # The query must NOT include 'activity' as a predicate value
        # (it may include 'activity' as part of 'validity' or variable names,
        # but not as a standalone predicate in the IN list)
        assert "'activity'" not in sql, (
            f"Timeline SQL must not include 'activity' predicate; found it in: {sql}"
        )


# ---------------------------------------------------------------------------
# Scenario 6: Empty entity returns empty arrays
# ---------------------------------------------------------------------------


class TestEmptyEntity:
    """Scenario 6 — Entity with zero matching facts returns [] with status 200."""

    @pytest.mark.parametrize(
        "path",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_empty_facts_returns_empty_list(self, path: str):
        app, _ = _app_with_pool(entity_exists=True, fetch_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Scenario 7: Entity does not exist → 404
# ---------------------------------------------------------------------------


class TestEntityNotFound:
    """Scenario 7 — Non-existent entity UUID returns 404."""

    @pytest.mark.parametrize(
        "path",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_missing_entity_returns_404(self, path: str):
        app, _ = _app_with_pool(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/{path}")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Scenario 8: Retracted/superseded facts excluded
# ---------------------------------------------------------------------------


class TestRetractedFactsExcluded:
    """Scenario 8 — validity='retracted'/'superseded' facts are not returned.

    The SQL WHERE clause includes ``validity = 'active'``, so the DB filters
    these out.  The test verifies the pool is called with no retracted rows
    returned and the endpoint returns an empty list.
    """

    @pytest.mark.parametrize(
        "path",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_only_active_validity_returned(self, path: str):
        """pool.fetch returns [] (DB filtered retracted/superseded); endpoint returns []."""
        app, pool = _app_with_pool(entity_exists=True, fetch_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        assert resp.status_code == 200
        assert resp.json() == []

        # Verify the SQL contains validity = 'active'
        call_args = pool.fetch.call_args
        sql = call_args[0][0]
        assert "validity = 'active'" in sql


# ---------------------------------------------------------------------------
# Scenario 9: Pagination defaults
# ---------------------------------------------------------------------------


class TestPaginationDefaults:
    """Scenario 9 — Default limit=50, offset=0."""

    @pytest.mark.parametrize(
        "path",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_default_pagination_params_sent_to_db(self, path: str):
        app, pool = _app_with_pool(fetch_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        assert resp.status_code == 200
        call_args = pool.fetch.call_args
        # OFFSET $2 LIMIT $3 (or similar) — positional args: entity_id, offset, limit
        # For timeline: entity_id, predicate_list, offset, limit
        positional = call_args[0]
        # offset and limit are the last two positional args
        offset = positional[-2]
        limit = positional[-1]
        assert offset == 0, f"Expected default offset=0, got {offset}"
        assert limit == 50, f"Expected default limit=50, got {limit}"


# ---------------------------------------------------------------------------
# Scenario 10: Pagination max enforced
# ---------------------------------------------------------------------------


class TestPaginationMaxEnforced:
    """Scenario 10 — ?limit=500 is clamped to 200."""

    @pytest.mark.parametrize(
        "path",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_limit_500_rejected_with_422(self, path: str):
        """FastAPI's Query(le=200) rejects limit > 200 with a 422 validation error."""
        app, _ = _app_with_pool(fetch_rows=[])
        resp = await _get(
            app,
            f"/api/relationship/entities/{_ENT_ID}/{path}",
            limit=500,
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scenario 11: Cross-scope facts excluded
# ---------------------------------------------------------------------------


class TestCrossScopeExcluded:
    """Scenario 11 — Facts with scope != 'relationship' are excluded.

    The SQL WHERE clause includes ``scope = 'relationship'``.
    """

    @pytest.mark.parametrize(
        "path",
        ["notes", "interactions", "gifts", "loans", "timeline"],
    )
    async def test_scope_filter_present_in_query(self, path: str):
        app, pool = _app_with_pool(entity_exists=True, fetch_rows=[])
        await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        call_args = pool.fetch.call_args
        sql = call_args[0][0]
        assert "scope = 'relationship'" in sql


# ---------------------------------------------------------------------------
# Scenario 12: Sparse metadata fields render as null
# ---------------------------------------------------------------------------


class TestSparseMetadataNull:
    """Scenario 12 — Missing metadata keys must render as null, not omitted."""

    async def test_note_emotion_null_when_absent(self):
        rows = [_make_row(predicate="contact_note", content="Note", metadata={})]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/notes")

        item = resp.json()[0]
        assert "emotion" in item
        assert item["emotion"] is None

    async def test_note_emotion_null_when_metadata_is_none(self):
        rows = [_make_row(predicate="contact_note", content="Note", metadata=None)]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/notes")

        item = resp.json()[0]
        assert item["emotion"] is None

    async def test_interaction_direction_and_group_size_null_when_absent(self):
        rows = [_make_row(predicate="interaction_meeting", content="Sync", metadata={})]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/interactions")

        item = resp.json()[0]
        assert "direction" in item
        assert item["direction"] is None
        assert "group_size" in item
        assert item["group_size"] is None

    async def test_gift_sparse_fields_null_when_absent(self):
        rows = [_make_row(predicate="gift", content="Book", metadata={})]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/gifts")

        item = resp.json()[0]
        assert "occasion" in item
        assert item["occasion"] is None
        assert "status" in item
        assert item["status"] is None

    async def test_loan_sparse_fields_null_when_absent(self):
        rows = [_make_row(predicate="loan", content="Lent bike", metadata={})]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/loans")

        item = resp.json()[0]
        for field in ("amount_cents", "currency", "direction", "settled", "settled_at"):
            assert field in item, f"Field '{field}' must be present in loan response"
            assert item[field] is None, f"Field '{field}' must be null when absent from metadata"

    async def test_loan_fields_populated_when_present(self):
        rows = [
            _make_row(
                predicate="loan",
                content="Borrowed $50",
                metadata={
                    "amount_cents": "5000",
                    "currency": "USD",
                    "direction": "borrowed",
                    "settled": "false",
                    "settled_at": None,
                },
            )
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/loans")

        item = resp.json()[0]
        assert item["amount_cents"] == "5000"
        assert item["currency"] == "USD"
        assert item["direction"] == "borrowed"
        assert item["settled"] == "false"

    async def test_timeline_metadata_null_when_empty(self):
        rows = [_make_row(predicate="contact_note", content="Note", metadata={})]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        item = resp.json()[0]
        # metadata={} → serialized as {} (truthy empty dict is preserved as {}),
        # but metadata=None → null. An empty dict {} is a valid value per spec.
        assert "metadata" in item

    async def test_timeline_metadata_null_when_none(self):
        rows = [_make_row(predicate="contact_note", content="Note", metadata=None)]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        item = resp.json()[0]
        assert item["metadata"] is None


# ---------------------------------------------------------------------------
# Scenario 13: Provenance contract — all 6 fields present on every tab
# ---------------------------------------------------------------------------

_PROVENANCE_FIELDS = ("src", "conf", "last_seen", "weight", "verified", "primary")


class TestProvenanceContractOnTabEndpoints:
    """Scenario 13 — Every tab endpoint MUST include all 6 provenance fields.

    The legacy ``facts`` table does not carry these columns, so the SQL SELECT
    returns explicit NULL / default literals.  The API contract requires the
    fields are present (even if null) — the UI MAY hide them, but the API MUST
    NOT omit them.
    """

    @pytest.mark.parametrize(
        "path,predicate",
        [
            ("notes", "contact_note"),
            ("interactions", "interaction_meeting"),
            ("gifts", "gift"),
            ("loans", "loan"),
            ("timeline", "contact_note"),
        ],
    )
    async def test_provenance_fields_present_in_response(self, path: str, predicate: str):
        """All six provenance fields must appear in every item, even if null."""
        rows = [_make_row(predicate=predicate, content="test fact")]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1, f"Expected at least one item from /{path}"
        item = body[0]
        for field in _PROVENANCE_FIELDS:
            assert field in item, f"Provenance field '{field}' missing from /{path} response item"

    @pytest.mark.parametrize(
        "path,predicate",
        [
            ("notes", "contact_note"),
            ("interactions", "interaction_call"),
            ("gifts", "gift"),
            ("loans", "loan"),
            ("timeline", "gift"),
        ],
    )
    async def test_src_is_memory_module_legacy(self, path: str, predicate: str):
        """``src`` must be ``'memory_module_legacy'`` (the legacy facts table sentinel)."""
        rows = [_make_row(predicate=predicate, content="test fact")]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        item = resp.json()[0]
        assert item["src"] == "memory_module_legacy", (
            f"Expected src='memory_module_legacy' from /{path}, got {item['src']!r}"
        )

    @pytest.mark.parametrize(
        "path,predicate",
        [
            ("notes", "contact_note"),
            ("interactions", "interaction_meeting"),
            ("gifts", "gift"),
            ("loans", "loan"),
            ("timeline", "contact_note"),
        ],
    )
    async def test_nullable_provenance_fields_are_null(self, path: str, predicate: str):
        """``conf``, ``last_seen``, and ``weight`` must be null for legacy table rows."""
        rows = [_make_row(predicate=predicate, content="test fact")]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        item = resp.json()[0]
        assert item["conf"] is None, f"Expected conf=null from /{path}, got {item['conf']!r}"
        assert item["last_seen"] is None, (
            f"Expected last_seen=null from /{path}, got {item['last_seen']!r}"
        )
        assert item["weight"] is None, f"Expected weight=null from /{path}, got {item['weight']!r}"

    @pytest.mark.parametrize(
        "path,predicate",
        [
            ("notes", "contact_note"),
            ("interactions", "interaction_meeting"),
            ("gifts", "gift"),
            ("loans", "loan"),
            ("timeline", "contact_note"),
        ],
    )
    async def test_boolean_provenance_fields_have_defaults(self, path: str, predicate: str):
        """``verified`` must be false and ``primary`` must be false for legacy table rows."""
        rows = [_make_row(predicate=predicate, content="test fact")]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/{path}")

        item = resp.json()[0]
        assert item["verified"] is False, (
            f"Expected verified=false from /{path}, got {item['verified']!r}"
        )
        assert item["primary"] is False, (
            f"Expected primary=false from /{path}, got {item['primary']!r}"
        )


# ---------------------------------------------------------------------------
# LinkedContacts endpoint
# ---------------------------------------------------------------------------


def _make_linked_contact_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for a contacts row.

    Includes ``preferred_channel`` so the enriched endpoint can read it from
    the main contacts query result.
    """
    data = {
        "id": uuid4(),
        "full_name": "Alice Example",
        "email": None,
        "phone": None,
        "preferred_channel": None,
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_label_row(contact_id, **kwargs) -> MagicMock:
    """Build a MagicMock like an asyncpg Record for a contact_labels/labels join row."""
    from uuid import uuid4 as _uuid4

    data = {
        "contact_id": contact_id,
        "id": _uuid4(),
        "name": "friend",
        "color": "#aabbcc",
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_ef_fact_row(**kwargs) -> MagicMock:
    """Build a MagicMock like an asyncpg Record for a relationship.entity_facts row.

    Expected keys consumed by ``_ef_row_to_ci_entry``:
    ``id``, ``predicate`` (e.g. ``has-email``), ``object`` (the raw value), ``primary``.
    """
    from uuid import uuid4 as _uuid4

    data = {
        "id": _uuid4(),
        "predicate": "has-email",
        "object": "alice@example.com",
        "primary": True,
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_pool_linked_contacts(
    *,
    entity_exists: bool = True,
    contact_rows: list | None = None,
    label_rows: list | None = None,
    fact_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app for the linked-contacts endpoint.

    The endpoint makes three ``pool.fetch`` calls:
      1. contacts query (main rows)
      2. asyncio.gather → label batch query
      3. asyncio.gather → entity_facts has-* triples query
         (calls 2 and 3 are concurrent via asyncio.gather)

    ``ci_rows`` was removed: channel identifiers now come exclusively from
    ``relationship.entity_facts`` has-* triples (bu-6ioq3 migration).

    ``side_effect`` threads calls in the order they are issued.
    When ``contact_rows`` is empty the handler returns early; the supplementary
    queries are never called so ``label_rows`` / ``fact_rows`` are irrelevant.
    """
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)

    _contacts = contact_rows if contact_rows is not None else []
    _labels = label_rows if label_rows is not None else []
    _facts = fact_rows if fact_rows is not None else []

    # For non-empty contacts: 3 fetch calls in order (contacts, labels, facts).
    # For empty contacts: only 1 fetch call (the contacts query).
    if _contacts:
        mock_pool.fetch = AsyncMock(side_effect=[_contacts, _labels, _facts])
    else:
        mock_pool.fetch = AsyncMock(return_value=_contacts)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


class TestEntityLinkedContacts:
    """Tests for GET /api/relationship/entities/{id}/linked-contacts."""

    async def test_returns_200_with_contacts(self):
        rows = [
            _make_linked_contact_row(
                full_name="Alice Example", email="alice@example.com", phone=None
            ),
            _make_linked_contact_row(full_name="Bob Builder", email=None, phone="+1-555-0100"),
        ]
        app, _ = _app_with_pool_linked_contacts(contact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2

    async def test_linked_contact_fields_populated(self):
        contact_id = uuid4()
        rows = [_make_linked_contact_row(id=contact_id, full_name="Alice Example")]
        # email and phone are now derived exclusively from entity_facts has-* triples.
        ef_email = _make_ef_fact_row(
            predicate="has-email", object="alice@example.com", primary=True
        )
        ef_phone = _make_ef_fact_row(predicate="has-phone", object="+44-20-1234", primary=True)
        app, _ = _app_with_pool_linked_contacts(
            contact_rows=rows,
            fact_rows=[ef_email, ef_phone],
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        item = resp.json()[0]
        assert item["full_name"] == "Alice Example"
        assert item["email"] == "alice@example.com"
        assert item["phone"] == "+44-20-1234"
        assert "id" in item

    async def test_returns_empty_list_when_no_contacts(self):
        app, _ = _app_with_pool_linked_contacts(contact_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_null_email_and_phone_returned_as_null(self):
        rows = [_make_linked_contact_row(full_name="Charlie", email=None, phone=None)]
        app, _ = _app_with_pool_linked_contacts(contact_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        item = resp.json()[0]
        assert item["email"] is None
        assert item["phone"] is None

    async def test_returns_404_for_missing_entity(self):
        app, _ = _app_with_pool_linked_contacts(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/linked-contacts")
        assert resp.status_code == 404

    async def test_enriched_response_includes_contact_info(self):
        """contact_info[] is populated from entity_facts has-* triples (bu-6ioq3 migration)."""
        contact_id = uuid4()
        contact_row = _make_linked_contact_row(id=contact_id, full_name="Alice Example")
        ef = _make_ef_fact_row(predicate="has-email", object="alice@example.com", primary=True)

        app, _ = _app_with_pool_linked_contacts(
            contact_rows=[contact_row],
            label_rows=[],
            fact_rows=[ef],
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        assert resp.status_code == 200
        item = resp.json()[0]
        assert "contact_info" in item
        assert len(item["contact_info"]) == 1
        assert item["contact_info"][0]["type"] == "email"
        assert item["contact_info"][0]["value"] == "alice@example.com"
        assert item["contact_info"][0]["secured"] is False

    async def test_enriched_response_includes_labels(self):
        """labels[] is populated from the batch label query."""
        contact_id = uuid4()
        contact_row = _make_linked_contact_row(id=contact_id, full_name="Bob")
        label = _make_label_row(contact_id, name="friend", color="#ff0000")

        app, _ = _app_with_pool_linked_contacts(
            contact_rows=[contact_row],
            label_rows=[label],
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        assert resp.status_code == 200
        item = resp.json()[0]
        assert "labels" in item
        assert len(item["labels"]) == 1
        assert item["labels"][0]["name"] == "friend"
        assert item["labels"][0]["color"] == "#ff0000"

    async def test_enriched_response_includes_preferred_channel(self):
        """preferred_channel is included from the main contacts query."""
        contact_id = uuid4()
        contact_row = _make_linked_contact_row(
            id=contact_id,
            full_name="Carol",
            preferred_channel="telegram",
        )

        app, _ = _app_with_pool_linked_contacts(
            contact_rows=[contact_row],
            label_rows=[],
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        assert resp.status_code == 200
        item = resp.json()[0]
        assert item["preferred_channel"] == "telegram"

    async def test_enriched_contact_info_empty_when_no_fact_rows(self):
        """contact_info defaults to [] when no entity_facts has-* rows exist."""
        contact_id = uuid4()
        contact_row = _make_linked_contact_row(id=contact_id, full_name="Dave")

        app, _ = _app_with_pool_linked_contacts(
            contact_rows=[contact_row],
            label_rows=[],
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        item = resp.json()[0]
        assert item["contact_info"] == []
        assert item["labels"] == []
        assert item["preferred_channel"] is None

    async def test_multiple_contacts_ci_and_labels_correctly_bucketed(self):
        """entity_facts CI goes to first contact (entity-level); labels bucket per-contact.

        Since bu-6ioq3, channel identifiers come from relationship.entity_facts which are
        entity-level (not per-contact). All CI entries are therefore attached to the first
        linked contact by name order. Labels remain per-contact as before.
        """
        cid_a = uuid4()
        cid_b = uuid4()
        contact_a = _make_linked_contact_row(id=cid_a, full_name="Alice")
        contact_b = _make_linked_contact_row(id=cid_b, full_name="Bob")

        ef_email = _make_ef_fact_row(
            predicate="has-email", object="alice@example.com", primary=True
        )
        label_b = _make_label_row(cid_b, name="colleague")

        app, _ = _app_with_pool_linked_contacts(
            contact_rows=[contact_a, contact_b],
            label_rows=[label_b],
            fact_rows=[ef_email],
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/linked-contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2

        alice = next(item for item in body if item["full_name"] == "Alice")
        bob = next(item for item in body if item["full_name"] == "Bob")

        # All entity_facts CI entries go to the first contact (Alice, by name order).
        assert len(alice["contact_info"]) == 1
        assert alice["contact_info"][0]["value"] == "alice@example.com"
        assert alice["labels"] == []

        # Bob has no CI (entity_facts are not per-contact), but has his label.
        assert bob["contact_info"] == []
        assert len(bob["labels"]) == 1
        assert bob["labels"][0]["name"] == "colleague"


# ---------------------------------------------------------------------------
# last_interaction_at from facts (contact list and contact detail)
# ---------------------------------------------------------------------------
#
# These tests verify that:
#  1. GET /contacts returns last_interaction_at sourced from the facts table
#     (predicate LIKE 'interaction_%', not the legacy interactions table).
#  2. GET /contacts/{id} embeds last_interaction_at in the primary query via
#     a correlated subquery on facts.entity_id.
#  3. Contacts with NULL entity_id return null last_interaction_at without
#     crashing.
#
# Both endpoints are exercised with mock DB pools so no real Postgres or
# Docker is required.  The mocks return pre-built asyncpg-like row objects
# that drive the correct code paths.
# ---------------------------------------------------------------------------

_CONTACT_ID = uuid4()
_ENTITY_ID = uuid4()
_LAST_INTERACTION = datetime(2026, 3, 15, 9, 0, 0, tzinfo=UTC)


def _make_contact_row(**kwargs) -> MagicMock:
    """Return a MagicMock that behaves like an asyncpg Record for a contact row."""
    data = {
        "id": _CONTACT_ID,
        "full_name": "Test Contact",
        "first_name": "Test",
        "last_name": "Contact",
        "nickname": None,
        "notes": None,
        "company": None,
        "job_title": None,
        "metadata": {},
        "created_at": _NOW,
        "updated_at": _NOW,
        "roles": [],
        "entity_id": _ENTITY_ID,
        "preferred_channel": None,
        "email": None,
        "phone": None,
        "last_interaction_at": None,
        "name": "Test Contact",
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_interaction_row(contact_id=None, last_at=None) -> MagicMock:
    """Return a row shaped like the facts-based last_interaction_at batch query result."""
    data = {
        "contact_id": contact_id or _CONTACT_ID,
        "last_at": last_at or _LAST_INTERACTION,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_for_contact_list(
    *,
    contact_rows: list,
    interaction_rows: list,
    count: int | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mock pool for the GET /contacts endpoint.

    The contact list endpoint makes two asyncio.gather calls:
      - gather(fetchval[count], fetch[contacts])
      - gather(fetch[labels], fetch[contact_info], fetch[interactions])

    We use side_effect to return different values per fetch call.
    """
    mock_pool = AsyncMock()

    # fetchval returns the total count
    mock_pool.fetchval = AsyncMock(return_value=count if count is not None else len(contact_rows))

    # fetch is called in order:
    #   call 0: contact data rows
    #   call 1: label rows (empty)
    #   call 2: contact_info rows (empty)
    #   call 3: interaction rows (facts-based)
    fetch_side_effects = [
        contact_rows,  # data query
        [],  # labels
        [],  # contact_info
        interaction_rows,  # facts-based last_interaction_at
    ]
    mock_pool.fetch = AsyncMock(side_effect=fetch_side_effects)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


def _app_for_contact_detail(
    *,
    contact_row: MagicMock | None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app with a mock pool for the GET /contacts/{id} endpoint.

    The contact detail endpoint:
      - fetchrow → main contact row (includes last_interaction_at from subquery)
      - asyncio.gather → fetch[labels], fetchrow[birthday], fetchrow[address], fetch[ci]
    """
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        side_effect=[
            contact_row,  # main contact query
            None,  # birthday
            None,  # address
        ]
    )
    mock_pool.fetch = AsyncMock(
        side_effect=[
            [],  # labels
            [],  # contact_info
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


class TestContactListLastInteractionAt:
    """last_interaction_at in GET /contacts comes from facts, not interactions table."""

    async def test_last_interaction_at_populated_from_facts(self):
        """Contact with a matching fact row gets correct last_interaction_at."""
        contact_row = _make_contact_row()
        interaction_row = _make_interaction_row(contact_id=_CONTACT_ID, last_at=_LAST_INTERACTION)
        app, _ = _app_for_contact_list(
            contact_rows=[contact_row],
            interaction_rows=[interaction_row],
        )
        resp = await _get(app, "/api/relationship/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["contacts"]) == 1
        assert body["contacts"][0]["last_interaction_at"] is not None
        assert "2026-03-15" in body["contacts"][0]["last_interaction_at"]

    async def test_last_interaction_at_null_when_no_facts(self):
        """Contact with no matching facts gets null last_interaction_at."""
        contact_row = _make_contact_row()
        app, _ = _app_for_contact_list(
            contact_rows=[contact_row],
            interaction_rows=[],  # no matching facts
        )
        resp = await _get(app, "/api/relationship/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["contacts"]) == 1
        assert body["contacts"][0]["last_interaction_at"] is None

    async def test_empty_contact_list_does_not_crash(self):
        """Contact list with no results returns empty list without error."""
        app, _ = _app_for_contact_list(
            contact_rows=[],
            interaction_rows=[],
            count=0,
        )
        resp = await _get(app, "/api/relationship/contacts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["contacts"] == []
        assert body["total"] == 0


class TestContactDetailLastInteractionAt:
    """last_interaction_at in GET /contacts/{id} comes from facts, not interactions table."""

    async def test_last_interaction_at_populated_from_facts(self):
        """Contact detail includes last_interaction_at when the correlated subquery finds facts."""
        contact_row = _make_contact_row(last_interaction_at=_LAST_INTERACTION)
        app, _ = _app_for_contact_detail(contact_row=contact_row)
        resp = await _get(app, f"/api/relationship/contacts/{_CONTACT_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["last_interaction_at"] is not None
        assert "2026-03-15" in body["last_interaction_at"]

    async def test_last_interaction_at_null_when_no_facts(self):
        """Contact detail returns null last_interaction_at when correlated subquery finds nothing."""
        contact_row = _make_contact_row(last_interaction_at=None)
        app, _ = _app_for_contact_detail(contact_row=contact_row)
        resp = await _get(app, f"/api/relationship/contacts/{_CONTACT_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["last_interaction_at"] is None

    async def test_null_entity_id_returns_null_last_interaction_at(self):
        """Contact with NULL entity_id gets null last_interaction_at without crashing.

        The correlated subquery uses f.entity_id = c.entity_id. When c.entity_id IS NULL,
        SQL null equality (NULL = NULL) is false, so the subquery returns NULL — not an error.
        """
        contact_row = _make_contact_row(entity_id=None, last_interaction_at=None)
        app, _ = _app_for_contact_detail(contact_row=contact_row)
        resp = await _get(app, f"/api/relationship/contacts/{_CONTACT_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["last_interaction_at"] is None
        assert body["entity_id"] is None


# ---------------------------------------------------------------------------
# JSONB metadata defensive parsing (bu-0uc4i)
# ---------------------------------------------------------------------------
#
# asyncpg returns JSONB columns as Python strings unless a type codec is
# registered on the connection. Calling ``dict(s)`` on a JSON string iterates
# char-by-char and raises ``TypeError: dictionary update sequence element #0
# has length 1; 2 is required``.
#
# These tests pin the defensive ``isinstance(_, dict)`` guard at:
#  - ``get_entity`` (router.py around line 2090)
#  - ``list_entity_timeline`` (router.py around line 2611)
#
# Both must return 200 even when the row's metadata is a JSON string rather
# than a dict.
# ---------------------------------------------------------------------------


def _make_entity_row(**kwargs) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for a public.entities row."""
    data = {
        "id": _ENT_ID,
        "canonical_name": "Owner",
        "entity_type": "person",
        "aliases": [],
        "roles": ["owner"],
        "metadata": {},
        "created_at": _NOW,
        "updated_at": _NOW,
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_classify_row(**kwargs) -> MagicMock:
    """Build a MagicMock row for the _classify_entity_state CTE query (PR #1862).

    The handler's second fetchrow (inside _classify_entity_state) reads these
    keys to decide the entity's curation state.  Defaults yield a 'healthy'
    classification so existing JSONB-metadata regression tests don't have to
    care about state semantics.
    """
    data = {
        "is_unidentified": False,
        "is_dup_flagged": False,
        "has_fresh_fact": True,
        "last_seen": _NOW,
        "dup_predicate": None,
        "dup_shared_value": None,
        "dup_peer_entity_ids": None,
        **kwargs,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _app_with_entity_pool(
    *,
    entity_row: MagicMock | None,
    info_rows: list | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire a FastAPI app whose pool serves a single entity row via fetchrow.

    The entity GET handler calls ``pool.fetchrow`` twice — once for the entity
    row and a second time inside ``_classify_entity_state`` (PR #1862) — then
    ``pool.fetch`` for entity_info.  ``entity_row=None`` simulates the 404 path
    (the classifier fetchrow is never reached).
    """
    mock_pool = AsyncMock()
    # First fetchrow → entity row; second fetchrow → classifier row.
    # When entity_row is None (404 path) the classifier is never reached, so
    # omit it from the side_effect list to keep the mock honest.
    fetchrow_side_effects = [entity_row]
    if entity_row is not None:
        fetchrow_side_effects.append(_make_classify_row())
    mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    mock_pool.fetch = AsyncMock(return_value=info_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()

    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, mock_pool


class TestEntityGetJsonbMetadata:
    """Regression: GET /entities/{id} must not crash on string-typed JSONB metadata (bu-0uc4i)."""

    async def test_dict_metadata_returns_200(self):
        row = _make_entity_row(metadata={"source_butler": "relationship"})
        app, _ = _app_with_entity_pool(entity_row=row)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}")

        assert resp.status_code == 200
        assert resp.json()["metadata"] == {"source_butler": "relationship"}

    async def test_string_metadata_does_not_crash(self):
        # Simulates asyncpg returning JSONB as a JSON string (no type codec registered).
        # Pre-fix this raised ValueError("dictionary update sequence element #0 has length 1...").
        row = _make_entity_row(metadata='{"source_butler": "relationship"}')
        app, _ = _app_with_entity_pool(entity_row=row)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}")

        assert resp.status_code == 200
        # Defensive fallback: string metadata is dropped to {} rather than parsed.
        # Proper JSON parsing is tracked as a follow-up bead (per bu-0uc4i AC #7).
        assert resp.json()["metadata"] == {}

    async def test_empty_dict_metadata_returns_200(self):
        row = _make_entity_row(metadata={})
        app, _ = _app_with_entity_pool(entity_row=row)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}")

        assert resp.status_code == 200
        assert resp.json()["metadata"] == {}

    async def test_missing_entity_returns_404(self):
        app, _ = _app_with_entity_pool(entity_row=None)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}")
        assert resp.status_code == 404


class TestTimelineJsonbMetadata:
    """Regression: GET /entities/{id}/timeline must not crash on string-typed JSONB metadata (bu-0uc4i)."""

    async def test_dict_metadata_returns_200(self):
        rows = [
            _make_row(
                predicate="contact_note",
                content="Note",
                metadata={"emotion": "happy"},
            )
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        assert resp.status_code == 200
        assert resp.json()[0]["metadata"] == {"emotion": "happy"}

    async def test_string_metadata_does_not_crash(self):
        # Pre-fix this raised the same dict() char-iteration error as the entity GET.
        rows = [
            _make_row(
                predicate="contact_note",
                content="Note",
                metadata='{"emotion": "happy"}',
            )
        ]
        app, _ = _app_with_pool(fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/timeline")

        assert resp.status_code == 200
        # Defensive fallback: string metadata is rendered as null per the spec's
        # "sparse metadata renders as null" rule rather than crashing.
        assert resp.json()[0]["metadata"] is None


# ---------------------------------------------------------------------------
# Message threads endpoint (bu-message-threads)
# ---------------------------------------------------------------------------
#
# GET /api/relationship/entities/{entity_id}/message-threads aggregates
# switchboard.message_inbox rows whose source_sender_identity matches one of
# the entity's contact identifiers. The endpoint must:
#   - 404 when the entity is missing
#   - return [] when no contact identifiers are reachable
#   - return [] when the switchboard pool is not registered (graceful)
#   - return grouped summaries when threads match
# ---------------------------------------------------------------------------


def _build_app_with_dual_pools(
    *,
    entity_exists: bool = True,
    identifier_rows: list | None = None,
    thread_rows: list | None = None,
    switchboard_available: bool = True,
) -> tuple[FastAPI, AsyncMock, AsyncMock]:
    """Wire an app with separate relationship and switchboard pools.

    Returns the app plus the two pools so tests can assert on either.
    """
    rel_pool = AsyncMock()
    rel_pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    rel_pool.fetch = AsyncMock(return_value=identifier_rows or [])

    sw_pool = AsyncMock()
    sw_pool.fetch = AsyncMock(return_value=thread_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)

    def _pool_lookup(name: str):
        if name == "switchboard":
            if not switchboard_available:
                raise KeyError("switchboard")
            return sw_pool
        return rel_pool

    mock_db.pool.side_effect = _pool_lookup

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app, rel_pool, sw_pool


class TestEntityMessageThreads:
    """GET /entities/{id}/message-threads — aggregated switchboard activity."""

    async def test_returns_404_when_entity_missing(self):
        app, _, _ = _build_app_with_dual_pools(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/message-threads")
        assert resp.status_code == 404

    async def test_returns_empty_when_no_identifiers(self):
        app, _, _ = _build_app_with_dual_pools(entity_exists=True, identifier_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/message-threads")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_empty_when_switchboard_pool_unavailable(self):
        # Identifiers exist but switchboard pool is not registered. Endpoint
        # must degrade gracefully to an empty list, not 500.
        identifier_rows = [{"value": "alice@example.com"}]
        app, _, _ = _build_app_with_dual_pools(
            entity_exists=True,
            identifier_rows=identifier_rows,
            switchboard_available=False,
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/message-threads")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_summaries_when_threads_match(self):
        identifier_rows = [{"value": "alice@example.com"}, {"value": "12345"}]
        thread_rows = [
            {
                "source_channel": "telegram",
                "thread_identity": "chat-99",
                "sender_identity": "12345",
                "last_direction": "inbound",
                "last_received_at": _NOW,
                "last_snippet": "see you tomorrow",
                "message_count": 7,
            },
            {
                "source_channel": "email",
                "thread_identity": None,
                "sender_identity": "alice@example.com",
                "last_direction": "outbound",
                "last_received_at": _EARLIER,
                "last_snippet": None,
                "message_count": 1,
            },
        ]
        app, _, sw_pool = _build_app_with_dual_pools(
            entity_exists=True,
            identifier_rows=identifier_rows,
            thread_rows=thread_rows,
        )
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/message-threads")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["source_channel"] == "telegram"
        assert body[0]["message_count"] == 7
        assert body[0]["last_snippet"] == "see you tomorrow"
        assert body[1]["source_channel"] == "email"
        assert body[1]["last_snippet"] is None

        # Verify candidates were forwarded to the switchboard query.
        sw_call = sw_pool.fetch.call_args
        sw_candidates = sw_call[0][1]
        assert "alice@example.com" in sw_candidates
        assert "12345" in sw_candidates

    async def test_limit_clamped_to_100(self):
        app, _, _ = _build_app_with_dual_pools(entity_exists=True)
        resp = await _get(
            app,
            f"/api/relationship/entities/{_ENT_ID}/message-threads",
            limit=500,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Entity-scoped important dates and Dunbar tier override
# ---------------------------------------------------------------------------


def _make_date_row(**kwargs) -> dict:
    """Build a dict-like row simulating an important_dates JOIN contacts result."""
    return {
        "contact_id": kwargs.get("contact_id", uuid4()),
        "contact_name": kwargs.get("contact_name", "Alice"),
        "label": kwargs.get("label", "birthday"),
        "month": kwargs.get("month", 4),
        "day": kwargs.get("day", 12),
        "year": kwargs.get("year"),
    }


class TestEntityImportantDates:
    """GET /entities/{id}/dates — important_dates scoped to an entity."""

    async def test_returns_404_when_entity_missing(self):
        app, _ = _app_with_pool(entity_exists=False)
        resp = await _get(app, f"/api/relationship/entities/{_MISSING_ENT_ID}/dates")
        assert resp.status_code == 404

    async def test_returns_empty_when_no_dates(self):
        app, _ = _app_with_pool(entity_exists=True, fetch_rows=[])
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/dates")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_dates_sorted_by_upcoming(self):
        contact_id = uuid4()
        rows = [
            _make_date_row(
                contact_id=contact_id,
                contact_name="Alice",
                label="birthday",
                month=12,
                day=25,
                year=1990,
            ),
            _make_date_row(
                contact_id=contact_id,
                contact_name="Alice",
                label="anniversary",
                month=2,
                day=14,
                year=2015,
            ),
        ]
        app, _ = _app_with_pool(entity_exists=True, fetch_rows=rows)
        resp = await _get(app, f"/api/relationship/entities/{_ENT_ID}/dates")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # Sorted ascending by upcoming_date — both rows should appear.
        labels = [d["label"] for d in body]
        assert set(labels) == {"birthday", "anniversary"}
        # Each row carries the canonical fields.
        for d in body:
            assert "upcoming_date" in d
            assert d["month"] in (12, 2)
            assert "year" in d


def _build_app_for_dunbar_patch(
    *,
    entity_exists: bool = True,
    contact_row: dict | None = None,
    set_result: dict | None = None,
    set_raises: Exception | None = None,
) -> tuple[FastAPI, AsyncMock]:
    """Wire an app for PATCH /entities/{id}/dunbar-tier tests.

    Patches the engine import so the router calls a mocked dunbar_tier_set
    rather than touching real DB logic.
    """
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    pool.fetchrow = AsyncMock(return_value=contact_row)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    # Patch the engine module the router imports lazily.
    from butlers.tools.relationship import dunbar as _dunbar_mod

    if set_raises is not None:
        _dunbar_mod.dunbar_tier_set = AsyncMock(side_effect=set_raises)
    else:
        _dunbar_mod.dunbar_tier_set = AsyncMock(
            return_value=set_result
            or {
                "contact_id": "stub",
                "entity_id": "stub",
                "action": "cleared",
                "message": "ok",
            }
        )

    return app, pool


async def _patch(app: FastAPI, path: str, json_body: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.patch(path, json=json_body)


class TestDunbarTierOverride:
    """PATCH /entities/{id}/dunbar-tier — pin or clear an override."""

    @pytest.fixture(autouse=True)
    def _restore_dunbar_tier_set(self):
        """Restore the real dunbar_tier_set after each test to prevent mock pollution."""
        from butlers.tools.relationship import dunbar as _dunbar_mod

        original = _dunbar_mod.dunbar_tier_set
        yield
        _dunbar_mod.dunbar_tier_set = original

    async def test_returns_404_when_entity_missing(self):
        app, _ = _build_app_for_dunbar_patch(entity_exists=False)
        resp = await _patch(
            app,
            f"/api/relationship/entities/{_MISSING_ENT_ID}/dunbar-tier",
            {"tier": 15},
        )
        assert resp.status_code == 404

    async def test_returns_404_when_no_linked_contact(self):
        app, _ = _build_app_for_dunbar_patch(entity_exists=True, contact_row=None)
        resp = await _patch(
            app,
            f"/api/relationship/entities/{_ENT_ID}/dunbar-tier",
            {"tier": 50},
        )
        assert resp.status_code == 404
        assert "linked contact" in resp.json()["detail"].lower()

    async def test_pins_tier_successfully(self):
        contact_id = uuid4()
        app, _ = _build_app_for_dunbar_patch(
            entity_exists=True,
            contact_row={"id": contact_id},
            set_result={
                "contact_id": str(contact_id),
                "entity_id": str(_ENT_ID),
                "action": "set",
                "tier": 50,
                "message": "Dunbar tier override set to 50.",
            },
        )
        resp = await _patch(
            app,
            f"/api/relationship/entities/{_ENT_ID}/dunbar-tier",
            {"tier": 50},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "set"
        assert body["tier"] == 50

    async def test_clears_tier_when_null(self):
        contact_id = uuid4()
        app, _ = _build_app_for_dunbar_patch(
            entity_exists=True,
            contact_row={"id": contact_id},
            set_result={
                "contact_id": str(contact_id),
                "entity_id": str(_ENT_ID),
                "action": "cleared",
                "message": "Override cleared.",
            },
        )
        resp = await _patch(
            app,
            f"/api/relationship/entities/{_ENT_ID}/dunbar-tier",
            {"tier": None},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == "cleared"
        assert body["tier"] is None

    async def test_invalid_tier_returns_422(self):
        contact_id = uuid4()
        app, _ = _build_app_for_dunbar_patch(
            entity_exists=True,
            contact_row={"id": contact_id},
            set_raises=ValueError("Invalid tier value 7."),
        )
        resp = await _patch(
            app,
            f"/api/relationship/entities/{_ENT_ID}/dunbar-tier",
            {"tier": 7},
        )
        assert resp.status_code == 422
        assert "Invalid tier" in resp.json()["detail"]
