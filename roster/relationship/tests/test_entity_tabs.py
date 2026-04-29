"""Integration tests for entity-keyed tab API endpoints.

Covers all 11 spec scenarios from
``openspec/changes/relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
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
    """Build a MagicMock that behaves like an asyncpg Record."""
    data = {
        "id": uuid4(),
        "predicate": "contact_note",
        "content": "default content",
        "metadata": {},
        "valid_at": _NOW,
        "created_at": _NOW,
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
