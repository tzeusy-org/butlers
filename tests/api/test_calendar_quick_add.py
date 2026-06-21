"""Tests for the calendar natural-language quick-add parse endpoint (bu-wennwz).

The endpoint ``POST /api/calendar/workspace/parse-quick-add`` is parse-only:
it LLM-parses a free-text string into a draft event and NEVER writes. Coverage:

- parse success (mocked LLM) returns a populated draft;
- no-write guarantee: the parse path never invokes a calendar MCP create tool;
- degraded path: ``resolve_model`` returns ``None`` -> ``parse_available=false``,
  no draft, no LLM call;
- empty/blank input is rejected (422) and unparseable LLM output ->
  ``parse_available=false`` with a reason.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.calendar_workspace import _get_db_manager

pytestmark = pytest.mark.unit

_QUICK_ADD_URL = "/api/calendar/workspace/parse-quick-add"

# A non-None resolve_model() tuple: (runtime_type, model_id, extra_args,
# catalog_entry_id, session_timeout_s). Only "not None" matters for the endpoint.
_FAKE_MODEL = ("claude", "claude-cheap", [], "00000000-0000-0000-0000-000000000001", 60)


def _build_app(app) -> tuple:
    """Override the DB manager (shared pool) and a spy MCP manager.

    The MCP manager is wired so we can assert the parse path never calls a
    butler tool (the no-write guarantee). The pool itself is a plain AsyncMock;
    every DB read in this module is patched at the ``resolve_model`` /
    dispatcher seam, so the pool is never exercised directly.
    """
    pool = AsyncMock()
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool

    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(side_effect=AssertionError("parse must not call MCP"))

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr


async def _post(app, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(_QUICK_ADD_URL, json=payload)


# ---------------------------------------------------------------------------
# Parse success (mocked LLM) + no-write guarantee
# ---------------------------------------------------------------------------


async def test_parse_quick_add_returns_draft_and_never_writes(app):
    app, _, mock_mgr = _build_app(app)

    dispatcher = MagicMock()
    dispatcher.call = AsyncMock(
        return_value=(
            '{"title": "Lunch with Sarah", '
            '"start_at": "2026-06-26T13:00:00+08:00", '
            '"end_at": "2026-06-26T14:00:00+08:00", '
            '"all_day": false, "location": "Tartine", "description": "with Sarah"}'
        )
    )

    with (
        patch(
            "butlers.api.calendar.quick_add.resolve_model",
            new=AsyncMock(return_value=_FAKE_MODEL),
        ),
        patch(
            "butlers.api.calendar.quick_add.DiscretionDispatcher",
            return_value=dispatcher,
        ),
    ):
        resp = await _post(
            app,
            {"text": "lunch with Sarah Fri 1pm at Tartine", "timezone": "Asia/Singapore"},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["parse_available"] is True
    draft = data["draft"]
    assert draft["title"] == "Lunch with Sarah"
    assert draft["start_at"] == "2026-06-26T13:00:00+08:00"
    assert draft["end_at"] == "2026-06-26T14:00:00+08:00"
    assert draft["location"] == "Tartine"
    assert data["reason"] is None
    # No-write guarantee: the parse path never reached the MCP surface.
    mock_mgr.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Degraded path: no cheap-tier model configured
# ---------------------------------------------------------------------------


async def test_parse_quick_add_degraded_when_no_model(app):
    app, _, mock_mgr = _build_app(app)

    dispatcher = MagicMock()
    dispatcher.call = AsyncMock(side_effect=AssertionError("must not call LLM when no model"))

    with (
        patch(
            "butlers.api.calendar.quick_add.resolve_model",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.api.calendar.quick_add.DiscretionDispatcher",
            return_value=dispatcher,
        ),
    ):
        resp = await _post(app, {"text": "lunch with Sarah Fri 1pm at Tartine"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["parse_available"] is False
    assert data["draft"] is None
    assert isinstance(data["reason"], str) and data["reason"]
    dispatcher.call.assert_not_called()
    mock_mgr.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Blank input rejected at the model boundary (422)
# ---------------------------------------------------------------------------


async def test_parse_quick_add_blank_text_rejected(app):
    app, _, mock_mgr = _build_app(app)
    resp = await _post(app, {"text": "   "})
    assert resp.status_code == 422
    mock_mgr.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Unparseable LLM output -> parse_available=false, no draft
# ---------------------------------------------------------------------------


async def test_parse_quick_add_unparseable_output(app):
    app, _, mock_mgr = _build_app(app)

    dispatcher = MagicMock()
    # The model's "not an event" sentinel.
    dispatcher.call = AsyncMock(return_value='{"title": null}')

    with (
        patch(
            "butlers.api.calendar.quick_add.resolve_model",
            new=AsyncMock(return_value=_FAKE_MODEL),
        ),
        patch(
            "butlers.api.calendar.quick_add.DiscretionDispatcher",
            return_value=dispatcher,
        ),
    ):
        resp = await _post(app, {"text": "asdf qwer zxcv"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["parse_available"] is False
    assert data["draft"] is None
    assert isinstance(data["reason"], str) and data["reason"]
    mock_mgr.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# all_day coercion: a stringy "false" from the LLM must NOT become True
# ---------------------------------------------------------------------------


async def test_parse_quick_add_all_day_string_false_is_false(app):
    app, _, mock_mgr = _build_app(app)

    dispatcher = MagicMock()
    # LLMs sometimes emit booleans as strings; "false" must coerce to False,
    # not True (a naive bool("false") would be truthy).
    dispatcher.call = AsyncMock(return_value='{"title": "Standup", "all_day": "false"}')

    with (
        patch(
            "butlers.api.calendar.quick_add.resolve_model",
            new=AsyncMock(return_value=_FAKE_MODEL),
        ),
        patch(
            "butlers.api.calendar.quick_add.DiscretionDispatcher",
            return_value=dispatcher,
        ),
    ):
        resp = await _post(app, {"text": "standup tomorrow"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["parse_available"] is True
    assert data["draft"]["all_day"] is False
    mock_mgr.get_client.assert_not_called()
