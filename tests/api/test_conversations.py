"""Tests for dashboard conversation API endpoints.

Covers:
- GET /api/butlers/{name}/conversations — list with status filter + pagination
- POST /api/butlers/{name}/conversations — create + SSE stream
- POST /api/butlers/{name}/conversations/{id}/messages — follow-up + SSE
- PATCH /api/butlers/{name}/conversations/{id} — update title/status
- GET /api/butlers/{name}/conversations/{id}/messages — list messages
- GET /api/butlers/{name}/conversations/search — full-text search
- GET /api/butlers/{name}/conversations/summary — aggregate stats
- Discretion bypass constant presence
- Dashboard channel in routing contracts

Issue: bu-27mx
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.models.conversation import (
    ConversationMessage,
    ConversationStats,
    ConversationSummary,
    ConversationUpdateRequest,
)
from butlers.api.routers.conversations import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_CONV_ID = uuid4()
_MSG_ID = uuid4()
_BUTLER = "atlas"


def _make_conversation_row(
    *,
    conv_id=None,
    butler_name: str = _BUTLER,
    title: str = "Hello world",
    status: str = "active",
    message_count: int = 2,
    total_input_tokens: int = 100,
    total_output_tokens: int = 200,
    total_duration_ms: int = 1500,
) -> dict:
    """Build a dict mimicking a shared.dashboard_conversations row."""
    return {
        "id": conv_id or _CONV_ID,
        "butler_name": butler_name,
        "title": title,
        "status": status,
        "created_at": _NOW,
        "updated_at": _NOW,
        "message_count": message_count,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_duration_ms": total_duration_ms,
    }


def _make_message_row(
    *,
    msg_id=None,
    conv_id=None,
    role: str = "user",
    content: str = "Hello",
    model_name: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_ms: int | None = None,
    tool_calls: Any = None,
    error: str | None = None,
    session_id=None,
    request_id=None,
) -> dict:
    """Build a dict mimicking a shared.dashboard_messages row."""
    return {
        "id": msg_id or _MSG_ID,
        "conversation_id": conv_id or _CONV_ID,
        "role": role,
        "content": content,
        "created_at": _NOW,
        "session_id": session_id,
        "model_name": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "tool_calls": tool_calls,
        "error": error,
        "request_id": request_id,
    }


def _app_with_mock_db(
    app: FastAPI,
    *,
    fetch_rows: list[dict] | None = None,
    fetchval_result: int = 0,
    fetchrow_result: dict | None = None,
    execute_result=None,
) -> FastAPI:
    """Wire the app with a mocked DatabaseManager for conversation endpoints."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    async def test_returns_paginated_response(self, app: FastAPI):
        """Response must have 'data' array and 'meta' with pagination fields."""
        row = _make_conversation_row()
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]

    async def test_returns_conversation_fields(self, app: FastAPI):
        """Each conversation must have required fields."""
        row = _make_conversation_row()
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")

        assert resp.status_code == 200
        conv = resp.json()["data"][0]
        assert conv["butler_name"] == _BUTLER
        assert conv["title"] == "Hello world"
        assert conv["status"] == "active"
        assert "id" in conv
        assert "message_count" in conv
        assert "total_input_tokens" in conv
        assert "total_output_tokens" in conv
        assert "total_duration_ms" in conv

    async def test_empty_list_on_no_conversations(self, app: FastAPI):
        """Returns empty data array when butler has no conversations."""
        _app_with_mock_db(app, fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_503_when_shared_db_unavailable(self, app: FastAPI):
        """Returns 503 when the shared database pool is unavailable."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = RuntimeError("no shared pool")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")

        assert resp.status_code == 503

    async def test_default_status_is_active(self, app: FastAPI):
        """When no status param, only active conversations are returned."""
        _app_with_mock_db(app, fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations")

        assert resp.status_code == 200

    async def test_accepts_status_all(self, app: FastAPI):
        """?status=all is a valid parameter."""
        _app_with_mock_db(app, fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations?status=all")

        assert resp.status_code == 200

    async def test_accepts_status_archived(self, app: FastAPI):
        """?status=archived is a valid parameter."""
        row = _make_conversation_row(status="archived")
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations?status=archived")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations/search
# ---------------------------------------------------------------------------


class TestSearchConversations:
    async def test_returns_400_when_query_missing(self, app: FastAPI):
        """Missing 'q' parameter should return 400 VALIDATION_ERROR."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/search")

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["code"] == "VALIDATION_ERROR"

    async def test_returns_400_when_query_empty(self, app: FastAPI):
        """Empty 'q' parameter should return 400 VALIDATION_ERROR."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/search?q=")

        assert resp.status_code == 400

    async def test_returns_search_results(self, app: FastAPI):
        """Valid query returns search results with snippet."""
        row = _make_conversation_row()
        row["snippet"] = "...matching content..."
        # Mock the two calls: fetch (rows) + fetchval (count)
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[row])
        mock_pool.fetchval = AsyncMock(return_value=1)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/search?q=hello")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations/summary
# ---------------------------------------------------------------------------


class TestConversationSummary:
    async def test_returns_summary_fields(self, app: FastAPI):
        """Summary response must include all aggregate fields."""
        summary_row = {
            "total_conversations": 5,
            "active_conversations": 3,
            "total_messages": 42,
            "total_input_tokens": 1000,
            "total_output_tokens": 2000,
            "total_duration_ms": 5000,
        }
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=summary_row)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_conversations"] == 5
        assert body["active_conversations"] == 3
        assert body["total_messages"] == 42

    async def test_returns_zeros_when_no_conversations(self, app: FastAPI):
        """Returns zero-filled stats when butler has no conversations."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_conversations"] == 0


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/conversations/{id}/messages
# ---------------------------------------------------------------------------


class TestListMessages:
    async def test_returns_404_when_conversation_not_found(self, app: FastAPI):
        """404 when conversation doesn't exist or belongs to wrong butler."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)  # conv lookup returns None
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}/messages")

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    async def test_returns_paginated_messages(self, app: FastAPI):
        """Returns paginated message list with required fields."""
        conv_row = _make_conversation_row()
        msg_row = _make_message_row()

        mock_pool = AsyncMock()
        # fetchrow for conversation lookup, then fetchval for count, fetch for rows
        mock_pool.fetchrow = AsyncMock(return_value=conv_row)
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(return_value=[msg_row])
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}/messages")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert len(body["data"]) == 1
        msg = body["data"][0]
        assert msg["role"] == "user"
        assert msg["content"] == "Hello"


# ---------------------------------------------------------------------------
# PATCH /api/butlers/{name}/conversations/{id}
# ---------------------------------------------------------------------------


class TestUpdateConversation:
    async def test_update_title(self, app: FastAPI):
        """PATCH with title updates the title and returns updated conversation."""
        updated_row = _make_conversation_row(title="New title")
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=updated_row)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}",
                json={"title": "New title"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "New title"

    async def test_archive_conversation(self, app: FastAPI):
        """PATCH with status=archived archives the conversation."""
        archived_row = _make_conversation_row(status="archived")
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=archived_row)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}",
                json={"status": "archived"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "archived"

    async def test_returns_404_when_not_found(self, app: FastAPI):
        """404 when conversation doesn't exist or belongs to wrong butler."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}",
                json={"status": "archived"},
            )

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    async def test_returns_422_when_body_empty(self, app: FastAPI):
        """422 when neither title nor status is provided."""
        _app_with_mock_db(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}",
                json={},
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/conversations — SSE streaming
# ---------------------------------------------------------------------------


class TestCreateConversation:
    async def test_returns_sse_stream(self, app: FastAPI):
        """POST /conversations returns text/event-stream response."""
        conv_row = _make_conversation_row()
        msg_row = _make_message_row()

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value=None)
        # conversation_create -> execute (insert)
        # message_create -> execute (insert)
        # message_count_increment -> execute
        # message_create (assistant) -> execute
        # conversation_update_aggregates -> execute
        mock_pool.fetchrow = AsyncMock(return_value=msg_row)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        # Patch conversation_create and message_create to return correct dicts
        with (
            patch(
                "butlers.api.routers.conversations.conversation_create",
                new=AsyncMock(return_value=conv_row),
            ),
            patch(
                "butlers.api.routers.conversations.message_create",
                new=AsyncMock(return_value=msg_row),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_message_count_increment",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_update_aggregates",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/butlers/{_BUTLER}/conversations",
                    json={"message": "Hello butler"},
                )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    async def test_stream_contains_conversation_created_event(self, app: FastAPI):
        """SSE stream must contain a conversation_created event for new conversations."""
        conv_row = _make_conversation_row()
        msg_row = _make_message_row()

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        with (
            patch(
                "butlers.api.routers.conversations.conversation_create",
                new=AsyncMock(return_value=conv_row),
            ),
            patch(
                "butlers.api.routers.conversations.message_create",
                new=AsyncMock(return_value=msg_row),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_message_count_increment",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_update_aggregates",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/butlers/{_BUTLER}/conversations",
                    json={"message": "Hello butler"},
                )

        content = resp.text
        assert "event: conversation_created" in content
        assert "event: done" in content

    async def test_stream_contains_message_complete_event(self, app: FastAPI):
        """SSE stream must contain message_complete event."""
        conv_row = _make_conversation_row()
        msg_row = _make_message_row()

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        with (
            patch(
                "butlers.api.routers.conversations.conversation_create",
                new=AsyncMock(return_value=conv_row),
            ),
            patch(
                "butlers.api.routers.conversations.message_create",
                new=AsyncMock(return_value=msg_row),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_message_count_increment",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_update_aggregates",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/butlers/{_BUTLER}/conversations",
                    json={"message": "Hello butler"},
                )

        content = resp.text
        assert "event: message_complete" in content

    async def test_returns_503_when_db_unavailable(self, app: FastAPI):
        """503 when shared DB is unavailable during creation."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = RuntimeError("db down")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/butlers/{_BUTLER}/conversations",
                json={"message": "Hello"},
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/conversations/{id}/messages — follow-up SSE
# ---------------------------------------------------------------------------


class TestSendMessage:
    async def test_returns_404_when_conversation_not_found(self, app: FastAPI):
        """404 when conversation doesn't exist."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}/messages",
                json={"message": "Follow up"},
            )

        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "CONVERSATION_NOT_FOUND"

    async def test_returns_sse_stream(self, app: FastAPI):
        """POST /messages returns text/event-stream response."""
        conv_row = _make_conversation_row()
        msg_row = _make_message_row()

        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value=None)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        with (
            patch(
                "butlers.api.routers.conversations.conversation_get",
                new=AsyncMock(return_value=conv_row),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_unarchive_if_needed",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.api.routers.conversations.message_list",
                new=AsyncMock(return_value=([], 0)),
            ),
            patch(
                "butlers.api.routers.conversations.message_create",
                new=AsyncMock(return_value=msg_row),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_message_count_increment",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.api.routers.conversations.conversation_update_aggregates",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/api/butlers/{_BUTLER}/conversations/{_CONV_ID}/messages",
                    json={"message": "Follow up"},
                )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        content = resp.text
        # Follow-up messages must NOT have conversation_created event
        assert "event: conversation_created" not in content
        assert "event: done" in content


# ---------------------------------------------------------------------------
# Pydantic model structure tests
# ---------------------------------------------------------------------------


class TestConversationModels:
    def test_conversation_summary_fields(self):
        """ConversationSummary must have all required fields."""
        data = _make_conversation_row()
        summary = ConversationSummary(**data)
        assert summary.butler_name == _BUTLER
        assert summary.title == "Hello world"
        assert summary.status == "active"
        assert summary.message_count == 2

    def test_conversation_message_fields(self):
        """ConversationMessage must accept all optional fields as None."""
        data = _make_message_row()
        msg = ConversationMessage(**data)
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.model_name is None
        assert msg.tool_calls is None

    def test_conversation_update_request_title_only(self):
        """ConversationUpdateRequest accepts title-only update."""
        req = ConversationUpdateRequest(title="New title")
        assert req.title == "New title"
        assert req.status is None

    def test_conversation_update_request_status_only(self):
        """ConversationUpdateRequest accepts status-only update."""
        req = ConversationUpdateRequest(status="archived")
        assert req.status == "archived"
        assert req.title is None

    def test_conversation_stats_fields(self):
        """ConversationStats has all required aggregate fields."""
        stats = ConversationStats(
            total_conversations=5,
            active_conversations=3,
            total_messages=42,
            total_input_tokens=1000,
            total_output_tokens=2000,
            total_duration_ms=5000,
        )
        assert stats.total_conversations == 5
        assert stats.active_conversations == 3


# ---------------------------------------------------------------------------
# Discretion bypass tests (task 7)
# ---------------------------------------------------------------------------


class TestDiscretionBypass:
    def test_dashboard_channel_in_bypass_set(self):
        """DISCRETION_BYPASS_CHANNELS must include 'dashboard'."""
        from butlers.connectors.discretion import DISCRETION_BYPASS_CHANNELS

        assert "dashboard" in DISCRETION_BYPASS_CHANNELS

    def test_dashboard_channel_in_source_channel_literal(self):
        """SourceChannel Literal must include 'dashboard'."""
        from butlers.tools.switchboard.routing.contracts import _ALLOWED_PROVIDERS_BY_CHANNEL

        assert "dashboard" in _ALLOWED_PROVIDERS_BY_CHANNEL

    def test_dashboard_channel_allows_internal_provider(self):
        """Dashboard channel must accept 'internal' as provider."""
        from butlers.tools.switchboard.routing.contracts import _ALLOWED_PROVIDERS_BY_CHANNEL

        assert "internal" in _ALLOWED_PROVIDERS_BY_CHANNEL["dashboard"]

    def test_dashboard_envelope_validates_correctly(self):
        """Dashboard envelope must pass IngestEnvelopeV1 validation."""
        from butlers.api.conversation_envelope import build_dashboard_envelope
        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        conv_id = uuid4()
        msg_id = uuid4()
        envelope = build_dashboard_envelope(
            conversation_id=conv_id,
            message_id=msg_id,
            message_text="Hello butler",
        )
        # Should not raise
        parsed = parse_ingest_envelope(envelope)
        assert parsed.source.channel == "dashboard"
        assert parsed.source.provider == "internal"
        assert parsed.control.policy_tier == "interactive"
        assert parsed.control.ingestion_tier == "full"

    def test_dashboard_envelope_endpoint_identity_format(self):
        """Envelope endpoint_identity must follow dashboard:web:{conversation_id} format."""
        from butlers.api.conversation_envelope import build_dashboard_envelope

        conv_id = uuid4()
        msg_id = uuid4()
        envelope = build_dashboard_envelope(
            conversation_id=conv_id,
            message_id=msg_id,
            message_text="Hello",
        )
        assert envelope["source"]["endpoint_identity"] == f"dashboard:web:{conv_id}"

    def test_dashboard_envelope_thread_id_equals_conversation_id(self):
        """Envelope external_thread_id must equal conversation_id."""
        from butlers.api.conversation_envelope import build_dashboard_envelope

        conv_id = uuid4()
        msg_id = uuid4()
        envelope = build_dashboard_envelope(
            conversation_id=conv_id,
            message_id=msg_id,
            message_text="Hello",
        )
        assert envelope["event"]["external_thread_id"] == str(conv_id)


# ---------------------------------------------------------------------------
# SSE event format unit tests
# ---------------------------------------------------------------------------


class TestSSEEventFormat:
    def test_sse_event_format(self):
        """SSE events must follow 'event: type\\ndata: json\\n\\n' format."""
        from butlers.api.routers.conversations import _sse_event

        chunk = _sse_event("token", {"content": "hello"})
        assert chunk.startswith("event: token\n")
        assert "data:" in chunk
        assert chunk.endswith("\n\n")

    def test_sse_keepalive_format(self):
        """Keepalive comment must be ': keepalive\\n\\n'."""
        from butlers.api.routers.conversations import _sse_comment

        chunk = _sse_comment("keepalive")
        assert chunk == ": keepalive\n\n"

    def test_sse_done_event(self):
        """Done event must be 'event: done'."""
        from butlers.api.routers.conversations import _sse_done

        chunk = _sse_done()
        assert "event: done" in chunk

    def test_sse_error_event(self):
        """Error event must include code and message."""
        from butlers.api.routers.conversations import _sse_error

        chunk = _sse_error("SESSION_FAILED", "Butler crashed")
        assert "event: error" in chunk
        assert "SESSION_FAILED" in chunk
        assert "Butler crashed" in chunk


# ---------------------------------------------------------------------------
# Conversation envelope unit tests
# ---------------------------------------------------------------------------


class TestConversationEnvelope:
    def test_envelope_without_context(self):
        """Envelope for first message has no preamble in normalized_text."""
        from butlers.api.conversation_envelope import build_dashboard_envelope

        conv_id = uuid4()
        msg_id = uuid4()
        envelope = build_dashboard_envelope(
            conversation_id=conv_id,
            message_id=msg_id,
            message_text="Hello",
        )
        assert envelope["payload"]["normalized_text"] == "Hello"

    def test_envelope_with_context_includes_preamble(self):
        """Follow-up envelope includes conversation history preamble."""
        from butlers.api.conversation_envelope import build_dashboard_envelope

        conv_id = uuid4()
        msg_id = uuid4()
        history = [
            {"role": "user", "content": "What's the weather?"},
            {"role": "assistant", "content": "It's sunny today!"},
        ]
        envelope = build_dashboard_envelope(
            conversation_id=conv_id,
            message_id=msg_id,
            message_text="Tell me more",
            conversation_context=history,
        )
        text = envelope["payload"]["normalized_text"]
        assert "Conversation history" in text
        assert "What's the weather?" in text
        assert "It's sunny today!" in text
        assert "Tell me more" in text

    def test_auto_title_truncates_at_word_boundary(self):
        """Auto-generated title truncates long messages at word boundary."""
        from butlers.api.conversations import _auto_title

        long_msg = (
            "This is a very long message that exceeds the eighty character limit set for titles"
        )
        title = _auto_title(long_msg)
        assert len(title) <= 82  # 80 + 1 for ellipsis char
        assert title.endswith("…")

    def test_auto_title_short_message_unchanged(self):
        """Short messages are used as-is for the title."""
        from butlers.api.conversations import _auto_title

        short = "Hello butler"
        assert _auto_title(short) == short
