"""Tests for the activity_v1 versioned read-model boundary.

Verifies:
- ``row_to_session`` converts a raw record to the typed DTO
- ``row_to_action`` converts a raw record to the typed DTO
- ``row_to_episode`` converts a raw record to the typed DTO
- ``query_activity_sessions`` returns typed DTOs and silently skips UndefinedTableError
- ``query_activity_actions`` returns typed DTOs and silently skips UndefinedTableError
- ``query_activity_episodes`` returns typed DTOs and silently skips UndefinedTableError
- Column constants are non-empty strings containing the expected column names
- Version marker is stable and matches the module name
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from asyncpg.exceptions import UndefinedTableError

from butlers.api.read_models.activity_v1 import (
    ACTION_COLUMNS,
    EPISODE_COLUMNS,
    READ_MODEL_VERSION,
    SESSION_COLUMNS,
    ActivityActionRow,
    ActivityEpisodeRow,
    ActivitySessionRow,
    query_activity_actions,
    query_activity_episodes,
    query_activity_sessions,
    row_to_action,
    row_to_episode,
    row_to_session,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_SESSION_ID = uuid4()
_ACTION_ID = uuid4()
_EPISODE_ID = uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(d: dict):
    """Wrap a dict in a MagicMock that supports subscript access."""
    m = MagicMock()
    m.__getitem__ = lambda self, k: d[k]
    return m


def _session_dict(**overrides) -> dict:
    base = {
        "id": _SESSION_ID,
        "prompt": "Check emails",
        "trigger_source": "scheduler",
        "success": True,
        "started_at": _NOW,
        "completed_at": _NOW,
        "duration_ms": 750,
    }
    base.update(overrides)
    return base


def _action_dict(**overrides) -> dict:
    base = {
        "id": _ACTION_ID,
        "tool_name": "send_email",
        "agent_summary": "Send weekly report",
        "status": "pending",
        "requested_at": _NOW,
        "session_id": _SESSION_ID,
    }
    base.update(overrides)
    return base


def _episode_dict(**overrides) -> dict:
    base = {
        "id": _EPISODE_ID,
        "content": "User prefers morning summaries",
        "importance": 7.5,
        "consolidation_status": "pending",
        "created_at": _NOW,
        "session_id": _SESSION_ID,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------


def test_version_marker_is_activity_v1():
    """READ_MODEL_VERSION must equal 'activity_v1' — change only on breaking update."""
    assert READ_MODEL_VERSION == "activity_v1"


# ---------------------------------------------------------------------------
# Column constants are non-empty strings with expected columns
# ---------------------------------------------------------------------------


def test_session_columns_is_non_empty_string():
    assert isinstance(SESSION_COLUMNS, str)
    assert len(SESSION_COLUMNS) > 0
    assert "completed_at" in SESSION_COLUMNS
    assert "started_at" in SESSION_COLUMNS
    assert "duration_ms" in SESSION_COLUMNS


def test_action_columns_is_non_empty_string():
    assert isinstance(ACTION_COLUMNS, str)
    assert len(ACTION_COLUMNS) > 0
    assert "tool_name" in ACTION_COLUMNS
    assert "requested_at" in ACTION_COLUMNS
    assert "session_id" in ACTION_COLUMNS


def test_episode_columns_is_non_empty_string():
    assert isinstance(EPISODE_COLUMNS, str)
    assert len(EPISODE_COLUMNS) > 0
    assert "content" in EPISODE_COLUMNS
    assert "created_at" in EPISODE_COLUMNS
    assert "consolidation_status" in EPISODE_COLUMNS


# ---------------------------------------------------------------------------
# row_to_session
# ---------------------------------------------------------------------------


def test_row_to_session_maps_all_fields():
    row = _make_record(_session_dict())
    dto = row_to_session(row)

    assert isinstance(dto, ActivitySessionRow)
    assert dto.id == _SESSION_ID
    assert dto.prompt == "Check emails"
    assert dto.trigger_source == "scheduler"
    assert dto.success is True
    assert dto.started_at == _NOW
    assert dto.completed_at == _NOW
    assert dto.duration_ms == 750


def test_row_to_session_none_prompt_allowed():
    row = _make_record(_session_dict(prompt=None))
    dto = row_to_session(row)
    assert dto.prompt is None


def test_row_to_session_none_completed_at_allowed():
    row = _make_record(_session_dict(completed_at=None))
    dto = row_to_session(row)
    assert dto.completed_at is None


# ---------------------------------------------------------------------------
# row_to_action
# ---------------------------------------------------------------------------


def test_row_to_action_maps_all_fields():
    row = _make_record(_action_dict())
    dto = row_to_action(row)

    assert isinstance(dto, ActivityActionRow)
    assert dto.id == _ACTION_ID
    assert dto.tool_name == "send_email"
    assert dto.agent_summary == "Send weekly report"
    assert dto.status == "pending"
    assert dto.requested_at == _NOW
    assert dto.session_id == _SESSION_ID


def test_row_to_action_none_agent_summary_allowed():
    row = _make_record(_action_dict(agent_summary=None))
    dto = row_to_action(row)
    assert dto.agent_summary is None


def test_row_to_action_none_session_id_allowed():
    row = _make_record(_action_dict(session_id=None))
    dto = row_to_action(row)
    assert dto.session_id is None


# ---------------------------------------------------------------------------
# row_to_episode
# ---------------------------------------------------------------------------


def test_row_to_episode_maps_all_fields():
    row = _make_record(_episode_dict())
    dto = row_to_episode(row)

    assert isinstance(dto, ActivityEpisodeRow)
    assert dto.id == _EPISODE_ID
    assert dto.content == "User prefers morning summaries"
    assert dto.importance == 7.5
    assert dto.consolidation_status == "pending"
    assert dto.created_at == _NOW
    assert dto.session_id == _SESSION_ID


def test_row_to_episode_none_content_allowed():
    row = _make_record(_episode_dict(content=None))
    dto = row_to_episode(row)
    assert dto.content is None


def test_row_to_episode_none_importance_allowed():
    row = _make_record(_episode_dict(importance=None))
    dto = row_to_episode(row)
    assert dto.importance is None


# ---------------------------------------------------------------------------
# query_activity_sessions
# ---------------------------------------------------------------------------


async def test_query_activity_sessions_returns_typed_dtos():
    """Returns a list of ActivitySessionRow DTOs from pool rows."""
    mock_pool = AsyncMock()
    row = _make_record(_session_dict())
    mock_pool.fetch = AsyncMock(return_value=[row])

    result = await query_activity_sessions(mock_pool, limit=10)

    assert len(result) == 1
    assert isinstance(result[0], ActivitySessionRow)
    assert result[0].id == _SESSION_ID


async def test_query_activity_sessions_passes_limit():
    """The limit value is passed as the query parameter."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    await query_activity_sessions(mock_pool, limit=5)

    call_args = mock_pool.fetch.call_args
    assert call_args[0][1] == 5  # second positional arg is the limit


async def test_query_activity_sessions_skips_undefined_table():
    """UndefinedTableError is caught and returns an empty list."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=UndefinedTableError("sessions"))

    result = await query_activity_sessions(mock_pool, limit=10)

    assert result == []


async def test_query_activity_sessions_returns_empty_list_when_no_rows():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    result = await query_activity_sessions(mock_pool, limit=10)

    assert result == []


# ---------------------------------------------------------------------------
# query_activity_actions
# ---------------------------------------------------------------------------


async def test_query_activity_actions_returns_typed_dtos():
    """Returns a list of ActivityActionRow DTOs from pool rows."""
    mock_pool = AsyncMock()
    row = _make_record(_action_dict())
    mock_pool.fetch = AsyncMock(return_value=[row])

    result = await query_activity_actions(mock_pool, limit=10)

    assert len(result) == 1
    assert isinstance(result[0], ActivityActionRow)
    assert result[0].id == _ACTION_ID


async def test_query_activity_actions_passes_limit():
    """The limit value is passed as the query parameter."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    await query_activity_actions(mock_pool, limit=7)

    call_args = mock_pool.fetch.call_args
    assert call_args[0][1] == 7


async def test_query_activity_actions_skips_undefined_table():
    """UndefinedTableError is caught and returns an empty list."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=UndefinedTableError("pending_actions"))

    result = await query_activity_actions(mock_pool, limit=10)

    assert result == []


# ---------------------------------------------------------------------------
# query_activity_episodes
# ---------------------------------------------------------------------------


async def test_query_activity_episodes_returns_typed_dtos():
    """Returns a list of ActivityEpisodeRow DTOs from pool rows."""
    mock_pool = AsyncMock()
    row = _make_record(_episode_dict())
    mock_pool.fetch = AsyncMock(return_value=[row])

    result = await query_activity_episodes(mock_pool, limit=10)

    assert len(result) == 1
    assert isinstance(result[0], ActivityEpisodeRow)
    assert result[0].id == _EPISODE_ID


async def test_query_activity_episodes_passes_limit():
    """The limit value is passed as the query parameter."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    await query_activity_episodes(mock_pool, limit=3)

    call_args = mock_pool.fetch.call_args
    assert call_args[0][1] == 3


async def test_query_activity_episodes_skips_undefined_table():
    """UndefinedTableError is caught and returns an empty list."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=UndefinedTableError("episodes"))

    result = await query_activity_episodes(mock_pool, limit=10)

    assert result == []
