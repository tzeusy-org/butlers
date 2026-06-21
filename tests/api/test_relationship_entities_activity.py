"""Tests for GET /api/relationship/entities/{id}/activity (activity aggregator).

Covers spec scenarios from
``openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/specs/dashboard-relationship/spec.md``
§ "Requirement: Entity activity aggregator (cross-butler read surface)".

Acceptance criteria:
1. Returns merged stream of relationship facts (src='relationship') and
   chronicler episodes (src='chronicler').
2. Sorted by timestamp descending (ts field).
3. Each row tagged with src field.
4. Owner-only authz read gate (Amendment 12b) — 403 when no owner entity.
5. Chronicler boundary: no direct SQL on chronicler.* tables (enforced by
   test_chronicler_boundary.py; this file tests the MCP mock path).
6. xfail test for /entities/{id}/activity in test_owner_authz_guardrail.py
   turns xpass once this endpoint ships.
7. Pagination (limit + offset) with total.
8. Graceful degrade when chronicler is unreachable.

Uses mock-pool pattern from test_relationship_entities_queue.py — no real
Postgres or Docker required.  Chronicler MCP is mocked at the client level.
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
from butlers.api.deps import get_mcp_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACTIVITY_PATH_TEMPLATE = "/api/relationship/entities/{entity_id}/activity"
BASE_URL = "http://test"

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
_OLDER = _NOW - timedelta(days=10)
_OLDEST = _NOW - timedelta(days=30)

_ENTITY_ID = uuid4()
_ACTIVITY_PATH = _ACTIVITY_PATH_TEMPLATE.format(entity_id=_ENTITY_ID)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _make_fact_row(
    *,
    fact_id: UUID | None = None,
    predicate: str = "contact_note",
    last_seen: datetime | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record for facts rows."""
    data = {
        "id": fact_id or uuid4(),
        "predicate": predicate,
        "last_seen": last_seen,
        "created_at": created_at or _NOW,
    }
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_owner_row() -> MagicMock:
    """Simulate a row returned by the owner-entity check query.

    Must include ``roles`` so that ``_get_owner_roles`` can inspect it.
    The endpoint uses ``_get_owner_roles`` which reads ``row["roles"]`` to
    determine whether the owner check passes.
    """
    data = {"id": uuid4(), "roles": ["owner"]}
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return row


def _make_chronicler_mcp_result(episodes: list[dict]) -> MagicMock:
    """Build a mock MCP tool result returning a list of episode dicts."""
    import json

    payload = json.dumps({"data": episodes, "count": len(episodes)})
    block = MagicMock()
    block.text = payload
    result = MagicMock()
    result.content = [block]
    result.is_error = False
    return result


def _make_chronicler_error_result() -> MagicMock:
    """Build a mock MCP tool result that signals an error."""
    result = MagicMock()
    result.content = []
    result.is_error = True
    return result


def _make_episode_dict(
    *,
    episode_id: UUID | None = None,
    canonical_start_at: datetime | None = None,
    canonical_title: str | None = "Test episode",
) -> dict:
    """Build a dict that matches the chronicler_list_episodes response format."""
    eid = episode_id or uuid4()
    return {
        "id": str(eid),
        "source_name": "test_source",
        "source_ref": "ref-001",
        "episode_type": "meeting",
        "start_at": (canonical_start_at or _NOW).isoformat(),
        "end_at": None,
        "precision": "exact",
        "title": canonical_title,
        "payload": {},
        "privacy": "normal",
        "retention_days": None,
        "tombstone_at": None,
        "canonical_start_at": (canonical_start_at or _NOW).isoformat(),
        "canonical_end_at": None,
        "canonical_title": canonical_title,
        "canonical_privacy": "normal",
        "corrected_at": None,
        "correction_note": None,
        "created_at": (canonical_start_at or _NOW).isoformat(),
        "updated_at": (canonical_start_at or _NOW).isoformat(),
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _app_with_mocks(
    *,
    owner_exists: bool = True,
    entity_exists: bool = True,
    fact_rows: list | None = None,
    chronicler_episodes: list[dict] | None = None,
    chronicler_unreachable: bool = False,
) -> tuple[FastAPI, AsyncMock, MagicMock]:
    """Wire a FastAPI app with mocked DB pool and MCP manager.

    Call sequence inside the endpoint:
      1. pool.fetchrow  → owner entity check (None → 403)
      2. pool.fetchval  → entity existence check (None → 404)
      3. pool.fetch     → relationship facts (asyncio.gather)
      4. mcp_manager.get_client('chronicler').call_tool(...)  → episodes (asyncio.gather)

    Returns (app, mock_pool, mock_mcp_manager).
    """
    mock_pool = AsyncMock()

    # Owner entity gate: fetchrow returns owner row or None.
    owner_row = _make_owner_row() if owner_exists else None

    # Entity existence gate: fetchval returns 1 or None.
    entity_val = 1 if entity_exists else None

    mock_pool.fetchrow = AsyncMock(return_value=owner_row)
    mock_pool.fetchval = AsyncMock(return_value=entity_val)
    mock_pool.fetch = AsyncMock(return_value=fact_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    # Mock MCP manager for chronicler.
    from butlers.api.deps import ButlerUnreachableError

    mock_mcp_manager = MagicMock()
    if chronicler_unreachable:
        mock_mcp_manager.get_client = AsyncMock(side_effect=ButlerUnreachableError("offline"))
    else:
        mock_client = AsyncMock()
        if chronicler_episodes is not None:
            mock_client.call_tool = AsyncMock(
                return_value=_make_chronicler_mcp_result(chronicler_episodes)
            )
        else:
            # Default: empty episodes.
            mock_client.call_tool = AsyncMock(return_value=_make_chronicler_mcp_result([]))
        mock_mcp_manager.get_client = AsyncMock(return_value=mock_client)

    app = create_app()
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    # Override the MCP manager dependency.  The activity endpoint uses
    # Depends(get_mcp_manager); we override it here so that the mock MCP
    # manager is injected without requiring init_dependencies() to have been
    # called.
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp_manager

    return app, mock_pool, mock_mcp_manager


async def _get(
    app: FastAPI,
    path: str = _ACTIVITY_PATH,
    **params,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, params=params or None)


# ---------------------------------------------------------------------------
# Scenario: Owner-only gate (Amendment 12b)
# ---------------------------------------------------------------------------


class TestOwnerGate:
    """Non-owner callers receive HTTP 403 + owner_required."""

    async def test_no_owner_returns_403(self):
        app, _, _ = _app_with_mocks(owner_exists=False)
        resp = await _get(app)
        assert resp.status_code == 403
        body = resp.json()
        code = body.get("code") or (body.get("detail") or {}).get("code")
        assert code == "owner_required"

    async def test_owner_present_allows_access(self):
        app, _, _ = _app_with_mocks(owner_exists=True, entity_exists=True)
        resp = await _get(app)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: Entity existence gate
# ---------------------------------------------------------------------------


class TestEntityExistence:
    """Returns 404 when the entity does not exist."""

    async def test_missing_entity_returns_404(self):
        app, _, _ = _app_with_mocks(owner_exists=True, entity_exists=False)
        resp = await _get(app)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Scenario: Empty entity — both sources empty
# ---------------------------------------------------------------------------


class TestEmptyActivity:
    """Entity with no facts and no chronicler episodes returns empty items."""

    async def test_empty_returns_200_with_empty_items(self):
        app, _, _ = _app_with_mocks(fact_rows=[], chronicler_episodes=[])
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
    """Activity response has correct top-level and per-item fields."""

    async def test_response_has_required_top_level_fields(self):
        fact_id = uuid4()
        fact_row = _make_fact_row(fact_id=fact_id, last_seen=_NOW)
        app, _, _ = _app_with_mocks(fact_rows=[fact_row], chronicler_episodes=[])
        resp = await _get(app)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "limit" in body
        assert "offset" in body

    async def test_relationship_entry_has_src_relationship(self):
        fact_row = _make_fact_row(predicate="contact_note", last_seen=_NOW)
        app, _, _ = _app_with_mocks(fact_rows=[fact_row], chronicler_episodes=[])
        resp = await _get(app)
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["src"] == "relationship"
        assert item["kind"] == "note"
        assert item["predicate"] == "contact_note"

    async def test_chronicler_entry_has_src_chronicler(self):
        ep_id = uuid4()
        episodes = [_make_episode_dict(episode_id=ep_id, canonical_start_at=_NOW)]
        app, _, _ = _app_with_mocks(fact_rows=[], chronicler_episodes=episodes)
        resp = await _get(app)
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["src"] == "chronicler"
        assert item["kind"] == "episode"
        assert item["episode_id"] == str(ep_id)
        assert item["summary"] == "Test episode"


# ---------------------------------------------------------------------------
# Scenario: Merged stream sort (timestamp descending)
# ---------------------------------------------------------------------------


class TestMergedStreamSort:
    """Items from both sources are merged and sorted by ts DESC."""

    async def test_relationship_and_chronicler_merged_sorted_desc(self):
        # fact at _OLDER, episode at _NOW — episode should appear first.
        fact_id = uuid4()
        ep_id = uuid4()
        fact_row = _make_fact_row(fact_id=fact_id, last_seen=_OLDER)
        episodes = [_make_episode_dict(episode_id=ep_id, canonical_start_at=_NOW)]
        app, pool, _ = _app_with_mocks(fact_rows=[fact_row], chronicler_episodes=episodes)
        resp = await _get(app)
        body = resp.json()
        assert body["total"] == 2
        items = body["items"]
        assert items[0]["src"] == "chronicler"
        assert items[1]["src"] == "relationship"
        # Guard: relationship SQL must use entity_facts, not the memory-module facts table.
        fetch_call_sql = pool.fetch.call_args_list[0][0][0]
        assert "relationship.entity_facts" in fetch_call_sql
        assert "relationship.facts" not in fetch_call_sql  # guard against regression

    async def test_multiple_items_sorted_desc(self):
        ep_old = uuid4()
        ep_new = uuid4()
        episodes = [
            _make_episode_dict(episode_id=ep_old, canonical_start_at=_OLDEST),
            _make_episode_dict(episode_id=ep_new, canonical_start_at=_NOW),
        ]
        app, _, _ = _app_with_mocks(fact_rows=[], chronicler_episodes=episodes)
        resp = await _get(app)
        body = resp.json()
        items = body["items"]
        assert items[0]["episode_id"] == str(ep_new)
        assert items[1]["episode_id"] == str(ep_old)


# ---------------------------------------------------------------------------
# Scenario: Chronicler unreachable — graceful degrade
# ---------------------------------------------------------------------------


class TestChroniclerDegrades:
    """Activity endpoint degrades gracefully when chronicler is unreachable."""

    async def test_chronicler_unreachable_still_returns_relationship_facts(self):
        fact_id = uuid4()
        fact_row = _make_fact_row(fact_id=fact_id, last_seen=_NOW)
        app, _, _ = _app_with_mocks(
            fact_rows=[fact_row],
            chronicler_unreachable=True,
        )
        resp = await _get(app)
        assert resp.status_code == 200
        body = resp.json()
        # Only relationship fact returned (no chronicler rows).
        assert body["total"] == 1
        assert body["items"][0]["src"] == "relationship"

    async def test_chronicler_error_result_degrades_gracefully(self):
        # Simulate MCP returning is_error=True.
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value=_make_chronicler_error_result())
        mock_mcp_manager = MagicMock()
        mock_mcp_manager.get_client = AsyncMock(return_value=mock_client)

        fact_row = _make_fact_row(last_seen=_NOW)
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_owner_row())
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(return_value=[fact_row])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        for butler_name, router_module in app.state.butler_routers:
            if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
                app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
                break
        app.dependency_overrides[get_mcp_manager] = lambda: mock_mcp_manager

        resp = await _get(app)
        assert resp.status_code == 200
        body = resp.json()
        # Only relationship rows; chronicler error was silently ignored.
        assert all(item["src"] == "relationship" for item in body["items"])


# ---------------------------------------------------------------------------
# Scenario: Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    """Pagination (limit + offset) slices the merged list correctly."""

    async def test_limit_respected(self):
        rows = [_make_fact_row(last_seen=_NOW - timedelta(seconds=i)) for i in range(5)]
        app, pool, _ = _app_with_mocks(fact_rows=rows, chronicler_episodes=[])
        resp = await _get(app, limit=3)
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 3
        assert body["limit"] == 3
        # Guard: relationship SQL must use entity_facts, not the memory-module facts table.
        fetch_call_sql = pool.fetch.call_args_list[0][0][0]
        assert "relationship.entity_facts" in fetch_call_sql
        assert "relationship.facts" not in fetch_call_sql  # guard against regression

    async def test_offset_respected(self):
        rows = [_make_fact_row(last_seen=_NOW - timedelta(seconds=i)) for i in range(5)]
        app, _, _ = _app_with_mocks(fact_rows=rows, chronicler_episodes=[])
        resp = await _get(app, offset=3)
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["offset"] == 3

    async def test_offset_beyond_total_returns_empty(self):
        fact_row = _make_fact_row(last_seen=_NOW)
        app, _, _ = _app_with_mocks(fact_rows=[fact_row], chronicler_episodes=[])
        resp = await _get(app, offset=100)
        body = resp.json()
        assert body["total"] == 1
        assert body["items"] == []


# ---------------------------------------------------------------------------
# Scenario: MCP call carries participant_entity_id filter (bu-xpv7f)
# ---------------------------------------------------------------------------


class TestChroniclerMcpCall:
    """The endpoint calls chronicler_list_episodes with participant_entity_id (not entity_id).

    Per design §D5, the aggregator uses the join-based participant_entity_id filter so
    meeting episodes where the entity is an attendee (not the calendar owner) surface in
    the entity's activity feed.
    """

    async def test_participant_episodes_surface_in_activity_feed(self):
        """A meeting episode where the entity is a PARTICIPANT (not owner) appears in activity.

        This tests the core requirement of §5.2: the aggregator must surface episodes
        where the requested entity is a participant, not just the calendar owner.

        The mock returns an episode (simulating one that matched via participant_entity_id
        in the join table). The test verifies the episode appears in the activity response
        and is tagged src='chronicler'.
        """
        participant_entity_id = uuid4()
        owner_entity_id = uuid4()  # Different entity — the calendar account owner

        # Build an episode dict that represents a meeting where our entity attended
        # but is NOT the owner (entity_id on the episode row would be owner_entity_id).
        ep_id = uuid4()
        episode = _make_episode_dict(
            episode_id=ep_id,
            canonical_start_at=_NOW,
            canonical_title="Team standup (participant view)",
        )

        # Wire the app with our participant entity as the target.
        activity_path = _ACTIVITY_PATH_TEMPLATE.format(entity_id=participant_entity_id)
        app, _, mock_mcp = _app_with_mocks(fact_rows=[], chronicler_episodes=[episode])
        resp = await _get(app, path=activity_path)

        assert resp.status_code == 200
        body = resp.json()

        # The episode must appear in the activity feed.
        assert body["total"] == 1
        item = body["items"][0]
        assert item["src"] == "chronicler"
        assert item["kind"] == "episode"
        assert item["episode_id"] == str(ep_id)
        assert item["summary"] == "Team standup (participant view)"

        # Verify the MCP call used participant_entity_id (join-based), not entity_id.
        mock_client = mock_mcp.get_client.return_value
        call_args = mock_client.call_tool.call_args
        tool_kwargs = call_args[0][1]
        assert tool_kwargs.get("participant_entity_id") == str(participant_entity_id)
        assert "entity_id" not in tool_kwargs, "Must not pass owner-only entity_id to the MCP call"
        # Confirm: the owner entity is NOT what was passed — this is the participant check.
        assert tool_kwargs.get("participant_entity_id") != str(owner_entity_id)


# ---------------------------------------------------------------------------
# Scenario: Predicate → kind mapping
# ---------------------------------------------------------------------------


class TestPredicateKindMapping:
    """Relationship facts are mapped to their correct kind."""

    @pytest.mark.parametrize(
        "predicate,expected_kind",
        [
            ("contact_note", "note"),
            ("interaction_meeting", "interaction"),
            ("interaction_call", "interaction"),
            ("interaction_email", "interaction"),
            ("gift", "gift"),
            ("loan", "loan"),
            ("life_event", "life_event"),
            ("dunbar_tier_override", "dunbar_tier_override"),
            ("unknown_predicate", "fact"),  # unmapped predicate → generic 'fact'
        ],
    )
    async def test_predicate_mapped_to_kind(self, predicate: str, expected_kind: str):
        row = _make_fact_row(predicate=predicate, last_seen=_NOW)
        app, _, _ = _app_with_mocks(fact_rows=[row], chronicler_episodes=[])
        resp = await _get(app)
        body = resp.json()
        assert body["items"][0]["kind"] == expected_kind
