"""Entity-keyed tab WRITE endpoints — notes, interactions, gifts, reach-out drafts.

bu-6t8ix.4: entity detail and Plex exposed notes / interactions / gifts as
GET-only, so the ``log-interaction``, ``gift-idea``, and ``draft-reach-out``
operator verbs had no write path (bu-86c4c.15 / PR #2894 deferred all three
rather than wire a button to nothing).

These tests cover the four POST routes that close that gap.  Each route
persists through the relationship butler's own fact-store tools — the same
``facts`` rows the sibling GET endpoints read — so a dashboard-authored record
is indistinguishable from a butler-authored one.  No new tables or predicates
columns are introduced.

The tools themselves are monkeypatched (they need a real pool plus the
embedding engine); these tests assert the HTTP contract: owner gate, entity
existence, argument marshalling, response shape, duplicate handling, and
validation errors.  This mirrors
``tests/api/test_api_health_medication_doses_nutrition.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

import butlers.tools.relationship.gifts as gifts_tools
import butlers.tools.relationship.interactions as interactions_tools
import butlers.tools.relationship.notes as notes_tools
import butlers.tools.relationship.reach_out as reach_out_tools
from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager

pytestmark = pytest.mark.unit

_ENT_ID = uuid.uuid4()
_NOW = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)
_BASE = "/api/relationship/entities"


def _make_app(
    *,
    caller_is_owner: bool = True,
    entity_exists: bool = True,
) -> tuple[FastAPI, AsyncMock, MagicMock]:
    """Wire an app whose relationship pool answers the owner and existence checks.

    ``fetchval`` backs ``_assert_entity_exists``; ``fetchrow`` backs
    ``_get_owner_roles``.  The MCP manager is a bare mock so a route that
    reached for a butler tool (i.e. tried to *send* something) would be
    visible as a recorded call rather than an exception.
    """
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1 if entity_exists else None)
    pool.fetchrow = AsyncMock(
        return_value={"id": _ENT_ID, "roles": ["owner"] if caller_is_owner else []}
    )
    pool.fetch = AsyncMock(return_value=[])

    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool

    app = create_app(api_key="")
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "relationship" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: db
            break

    mcp = MagicMock()
    app.dependency_overrides[get_mcp_manager] = lambda: mcp
    return app, pool, mcp


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _post(app: FastAPI, path: str, json_body: dict) -> httpx.Response:
    async with _client(app) as client:
        return await client.post(path, json=json_body)


def _assert_instant(value: str, expected: datetime) -> None:
    """Compare a serialised timestamp to *expected* as an instant.

    The dashboard API renders UTC timestamps with a ``Z`` suffix rather than
    ``+00:00``, so a raw string comparison against ``datetime.isoformat()``
    fails on formatting alone.
    """
    assert datetime.fromisoformat(value.replace("Z", "+00:00")) == expected


def _assert_owner_required(resp: httpx.Response) -> None:
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    body = resp.json()
    code = body.get("code") or (body.get("error") or {}).get("code")
    assert code == "owner_required", f"Expected owner_required, got {body}"


# ---------------------------------------------------------------------------
# POST /entities/{id}/notes
# ---------------------------------------------------------------------------


class TestCreateEntityNote:
    async def test_returns_201_and_note(self, monkeypatch):
        fact_id = uuid.uuid4()
        seen: dict = {}

        async def fake_create(pool, entity_id, content, *, emotion=None):
            seen.update(entity_id=entity_id, content=content, emotion=emotion)
            return {
                "id": fact_id,
                "entity_id": entity_id,
                "content": content,
                "emotion": emotion,
                "created_at": _NOW,
            }

        monkeypatch.setattr(notes_tools, "note_create_for_entity", fake_create)

        app, _, _ = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/notes",
            {"content": "Mentioned they are moving in October", "emotion": "warm"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] == str(fact_id)
        assert body["content"] == "Mentioned they are moving in October"
        assert body["emotion"] == "warm"
        _assert_instant(body["created_at"], _NOW)
        assert body["src"] == "memory_module_legacy"
        assert seen["entity_id"] == _ENT_ID

    async def test_duplicate_returns_409_with_existing_id(self, monkeypatch):
        existing = uuid.uuid4()

        async def fake_create(pool, entity_id, content, *, emotion=None):
            return {"skipped": "duplicate", "existing_id": str(existing)}

        monkeypatch.setattr(notes_tools, "note_create_for_entity", fake_create)

        app, _, _ = _make_app()
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/notes", {"content": "Same note"})

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "duplicate_note"
        assert detail["existing_id"] == str(existing)

    async def test_missing_entity_returns_404(self, monkeypatch):
        monkeypatch.setattr(notes_tools, "note_create_for_entity", AsyncMock())
        app, _, _ = _make_app(entity_exists=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/notes", {"content": "x"})
        assert resp.status_code == 404

    async def test_non_owner_returns_403(self, monkeypatch):
        monkeypatch.setattr(notes_tools, "note_create_for_entity", AsyncMock())
        app, _, _ = _make_app(caller_is_owner=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/notes", {"content": "x"})
        _assert_owner_required(resp)

    async def test_blank_content_rejected(self):
        app, _, _ = _make_app()
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/notes", {"content": "   "})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /entities/{id}/interactions  — the ``log-interaction`` verb
# ---------------------------------------------------------------------------


class TestCreateEntityInteraction:
    async def test_returns_201_and_interaction(self, monkeypatch):
        fact_id = uuid.uuid4()
        seen: dict = {}

        async def fake_log(pool, entity_id, type, **kwargs):
            seen.update(entity_id=entity_id, type=type, **kwargs)
            return {
                "id": fact_id,
                "entity_id": entity_id,
                "type": type,
                "summary": kwargs["summary"],
                "occurred_at": _NOW,
                "created_at": _NOW,
                "direction": kwargs["direction"],
                "duration_minutes": kwargs["duration_minutes"],
            }

        monkeypatch.setattr(interactions_tools, "interaction_log", fake_log)

        app, _, _ = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/interactions",
            {
                "type": "call",
                "summary": "Caught up about the move",
                "direction": "outgoing",
                "duration_minutes": 25,
            },
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] == str(fact_id)
        assert body["type"] == "call"
        assert body["summary"] == "Caught up about the move"
        assert body["direction"] == "outgoing"
        _assert_instant(body["occurred_at"], _NOW)
        assert seen["type"] == "call"
        assert seen["duration_minutes"] == 25

    async def test_explicit_occurred_at_is_forwarded(self, monkeypatch):
        when = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)
        seen: dict = {}

        async def fake_log(pool, entity_id, type, **kwargs):
            seen.update(kwargs)
            return {
                "id": uuid.uuid4(),
                "type": type,
                "summary": kwargs["summary"],
                "occurred_at": kwargs["occurred_at"],
                "created_at": _NOW,
                "direction": None,
                "duration_minutes": None,
            }

        monkeypatch.setattr(interactions_tools, "interaction_log", fake_log)

        app, _, _ = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/interactions",
            {"type": "meeting", "occurred_at": when.isoformat()},
        )

        assert resp.status_code == 201, resp.text
        assert seen["occurred_at"] == when

    async def test_duplicate_returns_409(self, monkeypatch):
        existing = uuid.uuid4()

        async def fake_log(pool, entity_id, type, **kwargs):
            return {"skipped": "duplicate", "existing_id": str(existing)}

        monkeypatch.setattr(interactions_tools, "interaction_log", fake_log)

        app, _, _ = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/interactions",
            {"type": "call", "occurred_at": _NOW.isoformat()},
        )

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "duplicate_interaction"
        assert detail["existing_id"] == str(existing)

    async def test_tool_value_error_returns_422(self, monkeypatch):
        async def fake_log(pool, entity_id, type, **kwargs):
            raise ValueError("Invalid direction 'sideways'.")

        monkeypatch.setattr(interactions_tools, "interaction_log", fake_log)

        app, _, _ = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/interactions",
            {"type": "call", "direction": "sideways"},
        )

        assert resp.status_code == 422, resp.text
        assert "sideways" in resp.json()["detail"]["message"]

    async def test_non_owner_returns_403(self, monkeypatch):
        monkeypatch.setattr(interactions_tools, "interaction_log", AsyncMock())
        app, _, _ = _make_app(caller_is_owner=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/interactions", {"type": "call"})
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self, monkeypatch):
        monkeypatch.setattr(interactions_tools, "interaction_log", AsyncMock())
        app, _, _ = _make_app(entity_exists=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/interactions", {"type": "call"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /entities/{id}/gifts  — the ``gift-idea`` verb
# ---------------------------------------------------------------------------


class TestCreateEntityGift:
    async def test_returns_201_and_gift(self, monkeypatch):
        fact_id = uuid.uuid4()
        seen: dict = {}

        async def fake_add(pool, entity_id, description, *, occasion=None):
            seen.update(entity_id=entity_id, description=description, occasion=occasion)
            return {
                "id": fact_id,
                "entity_id": entity_id,
                "description": description,
                "occasion": occasion,
                "status": "idea",
                "created_at": _NOW,
            }

        monkeypatch.setattr(gifts_tools, "gift_add_for_entity", fake_add)

        app, _, _ = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/gifts",
            {"description": "Hand-thrown mug", "occasion": "housewarming"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] == str(fact_id)
        assert body["description"] == "Hand-thrown mug"
        assert body["occasion"] == "housewarming"
        assert body["status"] == "idea"
        assert seen["entity_id"] == _ENT_ID

    async def test_duplicate_returns_409(self, monkeypatch):
        existing = uuid.uuid4()

        async def fake_add(pool, entity_id, description, *, occasion=None):
            return {"skipped": "duplicate", "existing_id": str(existing)}

        monkeypatch.setattr(gifts_tools, "gift_add_for_entity", fake_add)

        app, _, _ = _make_app()
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/gifts", {"description": "Hand-thrown mug"})

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "duplicate_gift"
        assert detail["existing_id"] == str(existing)

    async def test_blank_description_rejected(self):
        app, _, _ = _make_app()
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/gifts", {"description": " "})
        assert resp.status_code == 422

    async def test_non_owner_returns_403(self, monkeypatch):
        monkeypatch.setattr(gifts_tools, "gift_add_for_entity", AsyncMock())
        app, _, _ = _make_app(caller_is_owner=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/gifts", {"description": "Mug"})
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self, monkeypatch):
        monkeypatch.setattr(gifts_tools, "gift_add_for_entity", AsyncMock())
        app, _, _ = _make_app(entity_exists=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/gifts", {"description": "Mug"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST/GET /entities/{id}/reach-out-drafts  — the ``draft-reach-out`` verb
# ---------------------------------------------------------------------------


class TestReachOutDrafts:
    async def test_returns_201_with_draft_status(self, monkeypatch):
        fact_id = uuid.uuid4()
        seen: dict = {}

        async def fake_create(pool, entity_id, message, *, channel=None):
            seen.update(entity_id=entity_id, message=message, channel=channel)
            return {
                "id": fact_id,
                "entity_id": entity_id,
                "message": message,
                "channel": channel,
                "status": "draft",
                "created_at": _NOW,
            }

        monkeypatch.setattr(reach_out_tools, "reach_out_draft_create", fake_create)

        app, _, mcp = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/reach-out-drafts",
            {"message": "Been a while — coffee next week?", "channel": "telegram"},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] == str(fact_id)
        assert body["message"] == "Been a while — coffee next week?"
        assert body["channel"] == "telegram"
        assert body["status"] == "draft"
        assert seen["channel"] == "telegram"

    async def test_drafting_sends_nothing_externally(self, monkeypatch):
        """A draft is inert: no butler tool call, no MCP client, no send."""

        async def fake_create(pool, entity_id, message, *, channel=None):
            return {
                "id": uuid.uuid4(),
                "entity_id": entity_id,
                "message": message,
                "channel": channel,
                "status": "draft",
                "created_at": _NOW,
            }

        monkeypatch.setattr(reach_out_tools, "reach_out_draft_create", fake_create)

        app, _, mcp = _make_app()
        resp = await _post(
            app,
            f"{_BASE}/{_ENT_ID}/reach-out-drafts",
            {"message": "Hello there", "channel": "email"},
        )

        assert resp.status_code == 201, resp.text
        # The MCP manager is the only route out of this process to a butler
        # (and therefore to a channel).  It must be untouched.
        assert mcp.mock_calls == []

    async def test_duplicate_returns_409(self, monkeypatch):
        existing = uuid.uuid4()

        async def fake_create(pool, entity_id, message, *, channel=None):
            return {"skipped": "duplicate", "existing_id": str(existing)}

        monkeypatch.setattr(reach_out_tools, "reach_out_draft_create", fake_create)

        app, _, _ = _make_app()
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/reach-out-drafts", {"message": "Hello there"})

        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "duplicate_reach_out_draft"
        assert detail["existing_id"] == str(existing)

    async def test_blank_message_rejected(self):
        app, _, _ = _make_app()
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/reach-out-drafts", {"message": ""})
        assert resp.status_code == 422

    async def test_non_owner_returns_403(self, monkeypatch):
        monkeypatch.setattr(reach_out_tools, "reach_out_draft_create", AsyncMock())
        app, _, _ = _make_app(caller_is_owner=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/reach-out-drafts", {"message": "Hi there"})
        _assert_owner_required(resp)

    async def test_missing_entity_returns_404(self, monkeypatch):
        monkeypatch.setattr(reach_out_tools, "reach_out_draft_create", AsyncMock())
        app, _, _ = _make_app(entity_exists=False)
        resp = await _post(app, f"{_BASE}/{_ENT_ID}/reach-out-drafts", {"message": "Hi there"})
        assert resp.status_code == 404

    async def test_list_returns_drafts(self):
        fact_id = uuid.uuid4()
        app, pool, _ = _make_app()
        row = MagicMock()
        data = {
            "id": fact_id,
            "content": "Been a while — coffee next week?",
            "metadata": {"channel": "telegram", "status": "draft"},
            "created_at": _NOW,
        }
        row.__getitem__ = MagicMock(side_effect=lambda key: data[key])
        pool.fetch = AsyncMock(return_value=[row])

        async with _client(app) as client:
            resp = await client.get(f"{_BASE}/{_ENT_ID}/reach-out-drafts")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == str(fact_id)
        assert body[0]["channel"] == "telegram"
        assert body[0]["status"] == "draft"

    async def test_list_missing_entity_returns_404(self):
        app, _, _ = _make_app(entity_exists=False)
        async with _client(app) as client:
            resp = await client.get(f"{_BASE}/{_ENT_ID}/reach-out-drafts")
        assert resp.status_code == 404
