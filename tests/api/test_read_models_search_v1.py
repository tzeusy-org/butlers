"""Tests for the search_v1 versioned read-model boundary.

Verifies:
- ``READ_MODEL_VERSION`` is stable
- Column constants are non-empty strings with expected identifiers
- Row converter functions map asyncpg Records to typed DTOs
- ``query_entity_search`` returns a list of EntitySearchRow on success
- ``query_entity_search`` returns [] on exception
- ``query_contact_search`` returns a list of ContactSearchRow with snippet data
- ``query_contact_search`` returns [] on exception
- ``query_session_search`` returns a {butler: [SessionSearchRow]} mapping
- ``query_session_search`` returns {} on exception
- ``query_state_search`` returns a {butler: [StateSearchRow]} mapping
- ``query_state_search`` returns {} on exception
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from butlers.api.read_models.search_v1 import (
    CONTACT_COLUMNS,
    ENTITY_COLUMNS,
    ENTITY_FACTS_SNIPPET_COLUMNS,
    READ_MODEL_VERSION,
    SESSION_COLUMNS,
    STATE_COLUMNS,
    ContactSearchRow,
    EntitySearchRow,
    SessionSearchRow,
    StateSearchRow,
    query_contact_search,
    query_entity_search,
    query_session_search,
    query_state_search,
    row_to_contact,
    row_to_entity,
    row_to_session,
    row_to_state,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
_ENTITY_ID = UUID("00000000-0000-0000-0000-000000000001")
_CONTACT_ID = UUID("00000000-0000-0000-0000-000000000002")
_SESSION_ID = UUID("00000000-0000-0000-0000-000000000003")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(d: dict) -> MagicMock:
    """Wrap a dict in a MagicMock that supports subscript access."""
    m = MagicMock()
    m.__getitem__ = lambda self, k: d[k]
    return m


def _entity_dict(**overrides) -> dict:
    base = {
        "id": _ENTITY_ID,
        "canonical_name": "Alice Smith",
        "entity_type": "person",
        "aliases": ["alice", "al"],
    }
    base.update(overrides)
    return base


def _contact_dict(**overrides) -> dict:
    # After bu-tzyuh: rows come from public.entities; id == entity_id.
    base = {
        "id": _ENTITY_ID,
        "name": "Alice Smith",
        "entity_id": _ENTITY_ID,
    }
    base.update(overrides)
    return base


def _session_dict(**overrides) -> dict:
    base = {
        "id": _SESSION_ID,
        "prompt": "What is the weather?",
        "result": "It is sunny.",
        "trigger_source": "api",
        "success": True,
        "started_at": _NOW,
        "duration_ms": 1234,
        "matched_field": "prompt",
    }
    base.update(overrides)
    return base


def _state_dict(**overrides) -> dict:
    base = {
        "key": "user.prefs.theme",
        "value_text": "dark",
        "updated_at": _NOW - timedelta(hours=1),
        "matched_field": "key",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------


def test_version_marker_is_search_v1():
    assert READ_MODEL_VERSION == "search_v1"


# ---------------------------------------------------------------------------
# Column constants
# ---------------------------------------------------------------------------


def test_entity_columns_non_empty():
    assert isinstance(ENTITY_COLUMNS, str) and len(ENTITY_COLUMNS) > 0


def test_entity_columns_has_expected_identifiers():
    for col in ("id", "canonical_name", "entity_type", "aliases"):
        assert col in ENTITY_COLUMNS


def test_contact_columns_non_empty():
    assert isinstance(CONTACT_COLUMNS, str) and len(CONTACT_COLUMNS) > 0


def test_contact_columns_has_expected_identifiers():
    for col in ("id", "name", "entity_id"):
        assert col in CONTACT_COLUMNS
    # After bu-tzyuh: uses public.entities via 'e.' alias
    assert "public.contacts" not in CONTACT_COLUMNS
    assert "e." in CONTACT_COLUMNS


def test_entity_facts_snippet_columns_non_empty():
    assert isinstance(ENTITY_FACTS_SNIPPET_COLUMNS, str) and len(ENTITY_FACTS_SNIPPET_COLUMNS) > 0


def test_entity_facts_snippet_columns_has_expected_identifiers():
    for col in ("entity_id", "predicate", "object"):
        assert col in ENTITY_FACTS_SNIPPET_COLUMNS


def test_session_columns_has_matched_field_case():
    assert "matched_field" in SESSION_COLUMNS
    assert "CASE" in SESSION_COLUMNS
    assert "$1" in SESSION_COLUMNS


def test_state_columns_has_matched_field_case():
    assert "matched_field" in STATE_COLUMNS
    assert "CASE" in STATE_COLUMNS
    assert "$1" in STATE_COLUMNS


# ---------------------------------------------------------------------------
# row_to_entity
# ---------------------------------------------------------------------------


def test_row_to_entity_maps_all_fields():
    row = _make_record(_entity_dict())
    dto = row_to_entity(row)
    assert isinstance(dto, EntitySearchRow)
    assert dto.id == _ENTITY_ID
    assert dto.canonical_name == "Alice Smith"
    assert dto.entity_type == "person"
    assert dto.aliases == ["alice", "al"]


def test_row_to_entity_none_aliases_becomes_empty_list():
    row = _make_record(_entity_dict(aliases=None))
    dto = row_to_entity(row)
    assert dto.aliases == []


def test_row_to_entity_none_entity_type():
    row = _make_record(_entity_dict(entity_type=None))
    dto = row_to_entity(row)
    assert dto.entity_type is None


# ---------------------------------------------------------------------------
# row_to_contact
# ---------------------------------------------------------------------------


def test_row_to_contact_maps_all_fields():
    row = _make_record(_contact_dict())
    dto = row_to_contact(row)
    assert isinstance(dto, ContactSearchRow)
    assert dto.id == _ENTITY_ID
    assert dto.name == "Alice Smith"
    assert dto.entity_id == _ENTITY_ID
    assert dto.id == dto.entity_id  # same UUID after bu-tzyuh
    assert dto.email is None
    assert dto.phone is None


# ---------------------------------------------------------------------------
# row_to_session
# ---------------------------------------------------------------------------


def test_row_to_session_maps_all_fields():
    row = _make_record(_session_dict())
    dto = row_to_session(row)
    assert isinstance(dto, SessionSearchRow)
    assert dto.id == _SESSION_ID
    assert dto.prompt == "What is the weather?"
    assert dto.result == "It is sunny."
    assert dto.trigger_source == "api"
    assert dto.success is True
    assert dto.started_at == _NOW
    assert dto.duration_ms == 1234
    assert dto.matched_field == "prompt"


def test_row_to_session_matched_field_result():
    row = _make_record(_session_dict(matched_field="result"))
    dto = row_to_session(row)
    assert dto.matched_field == "result"


def test_row_to_session_none_optional_fields():
    row = _make_record(
        _session_dict(result=None, trigger_source=None, success=None, duration_ms=None)
    )
    dto = row_to_session(row)
    assert dto.result is None
    assert dto.trigger_source is None
    assert dto.success is None
    assert dto.duration_ms is None


# ---------------------------------------------------------------------------
# row_to_state
# ---------------------------------------------------------------------------


def test_row_to_state_maps_all_fields():
    row = _make_record(_state_dict())
    dto = row_to_state(row)
    assert isinstance(dto, StateSearchRow)
    assert dto.key == "user.prefs.theme"
    assert dto.value_text == "dark"
    assert dto.updated_at == _NOW - timedelta(hours=1)
    assert dto.matched_field == "key"


def test_row_to_state_matched_field_value():
    row = _make_record(_state_dict(matched_field="value"))
    dto = row_to_state(row)
    assert dto.matched_field == "value"


def test_row_to_state_none_optional_fields():
    row = _make_record(_state_dict(value_text=None, updated_at=None))
    dto = row_to_state(row)
    assert dto.value_text is None
    assert dto.updated_at is None


# ---------------------------------------------------------------------------
# query_entity_search — success
# ---------------------------------------------------------------------------


async def test_query_entity_search_returns_typed_dtos():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_make_record(_entity_dict())])

    result = await query_entity_search(mock_pool, "%alice%", 20)

    assert len(result) == 1
    assert isinstance(result[0], EntitySearchRow)
    assert result[0].canonical_name == "Alice Smith"


async def test_query_entity_search_empty_result():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])

    result = await query_entity_search(mock_pool, "%nomatch%", 20)
    assert result == []


async def test_query_entity_search_swallows_exception():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=RuntimeError("relation does not exist"))

    result = await query_entity_search(mock_pool, "%foo%", 5)
    assert result == []


# ---------------------------------------------------------------------------
# query_contact_search — success
# ---------------------------------------------------------------------------


async def test_query_contact_search_returns_typed_dtos():
    mock_pool = AsyncMock()
    entity_row = _make_record(_contact_dict())
    mock_pool.fetch = AsyncMock(side_effect=[[entity_row], []])  # second call: empty ef_rows
    result = await query_contact_search(mock_pool, "%alice%", 20)
    assert len(result) == 1
    assert isinstance(result[0], ContactSearchRow)
    assert result[0].name == "Alice Smith"
    assert result[0].id == _ENTITY_ID
    assert result[0].entity_id == _ENTITY_ID
    assert result[0].email is None
    assert result[0].phone is None


async def test_query_contact_search_populates_email_from_entity_facts():
    """Two-phase: contact has entity_id → email populated from entity_facts."""
    mock_pool = AsyncMock()
    contact_row = _make_record(_contact_dict())
    ef_row = _make_record(
        {"entity_id": _ENTITY_ID, "predicate": "has-email", "object": "alice@example.com"}
    )
    mock_pool.fetch = AsyncMock(side_effect=[[contact_row], [ef_row]])

    result = await query_contact_search(mock_pool, "%alice%", 20)

    assert result[0].email == "alice@example.com"
    assert result[0].phone is None


async def test_query_contact_search_populates_phone_from_entity_facts():
    """Two-phase: phone is populated from entity_facts has-phone predicate."""
    mock_pool = AsyncMock()
    contact_row = _make_record(_contact_dict())
    ef_row = _make_record(
        {"entity_id": _ENTITY_ID, "predicate": "has-phone", "object": "+1-555-0100"}
    )
    mock_pool.fetch = AsyncMock(side_effect=[[contact_row], [ef_row]])

    result = await query_contact_search(mock_pool, "%alice%", 20)

    assert result[0].phone == "+1-555-0100"
    assert result[0].email is None


async def test_query_contact_search_second_fetch_always_runs():
    """After bu-tzyuh all results have entity_id, so snippet fetch always runs."""
    mock_pool = AsyncMock()
    entity_row = _make_record(_contact_dict())  # entity_id always set
    mock_pool.fetch = AsyncMock(side_effect=[[entity_row], []])
    result = await query_contact_search(mock_pool, "%alice%", 20)
    assert mock_pool.fetch.call_count == 2
    assert result[0].email is None


async def test_query_contact_search_swallows_exception():
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=RuntimeError("permission denied"))

    result = await query_contact_search(mock_pool, "%foo%", 5)
    assert result == []


# ---------------------------------------------------------------------------
# query_session_search — fan-out
# ---------------------------------------------------------------------------


def _make_db_with_fan_out(result: dict) -> MagicMock:
    db = MagicMock()
    db.fan_out = AsyncMock(return_value=result)
    return db


async def test_query_session_search_returns_typed_dtos():
    db = _make_db_with_fan_out({"assistant": [_make_record(_session_dict())]})

    result = await query_session_search(db, "%weather%", 20)

    assert "assistant" in result
    assert len(result["assistant"]) == 1
    assert isinstance(result["assistant"][0], SessionSearchRow)


async def test_query_session_search_multiple_butlers():
    db = _make_db_with_fan_out(
        {
            "assistant": [_make_record(_session_dict())],
            "secretary": [_make_record(_session_dict(matched_field="result"))],
        }
    )

    result = await query_session_search(db, "%weather%", 20)

    assert len(result) == 2
    assert result["secretary"][0].matched_field == "result"


async def test_query_session_search_empty_fan_out_returns_empty_dict():
    db = _make_db_with_fan_out({})
    result = await query_session_search(db, "%nothing%", 20)
    assert result == {}


async def test_query_session_search_swallows_exception():
    db = MagicMock()
    db.fan_out = AsyncMock(side_effect=RuntimeError("fan-out failure"))

    result = await query_session_search(db, "%foo%", 5)
    assert result == {}


# ---------------------------------------------------------------------------
# query_state_search — fan-out
# ---------------------------------------------------------------------------


async def test_query_state_search_returns_typed_dtos():
    db = _make_db_with_fan_out({"assistant": [_make_record(_state_dict())]})

    result = await query_state_search(db, "%theme%", 20)

    assert "assistant" in result
    assert isinstance(result["assistant"][0], StateSearchRow)
    assert result["assistant"][0].key == "user.prefs.theme"


async def test_query_state_search_empty_fan_out_returns_empty_dict():
    db = _make_db_with_fan_out({})
    result = await query_state_search(db, "%nothing%", 20)
    assert result == {}


async def test_query_state_search_passes_pattern_and_limit():
    db = _make_db_with_fan_out({})
    await query_state_search(db, "%prefs%", 15)
    args = db.fan_out.call_args[0]
    assert args[1] == ("%prefs%", 15)


async def test_query_state_search_swallows_exception():
    db = MagicMock()
    db.fan_out = AsyncMock(side_effect=RuntimeError("fan-out failure"))

    result = await query_state_search(db, "%foo%", 5)
    assert result == {}
