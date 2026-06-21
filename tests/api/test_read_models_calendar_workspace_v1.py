"""Tests for the calendar_workspace_v1 versioned read-model boundary.

Verifies:
- ``READ_MODEL_VERSION`` is stable
- ``SOURCE_COLUMNS`` and ``WORKSPACE_COLUMNS`` are non-empty with expected identifiers
- ``row_to_source`` converts asyncpg Records to typed CalendarSourceRow DTOs
- ``row_to_workspace`` converts asyncpg Records to typed CalendarWorkspaceRow DTOs
- ``row_to_source`` applies the db_butler fallback when butler_name is NULL
- ``query_calendar_sources`` returns flat list of CalendarSourceRow from fan-out
- ``query_calendar_sources`` builds correct dynamic WHERE with lane/butlers/sources
- ``query_calendar_workspace`` returns flat list of CalendarWorkspaceRow from fan-out
- ``query_calendar_workspace`` builds correct dynamic WHERE with butlers/sources
- Both query functions use ``db.butlers_with_module("calendar")`` when butlers=None
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from butlers.api.read_models.calendar_workspace_v1 import (
    PROPOSAL_COLUMNS,
    READ_MODEL_VERSION,
    SOURCE_COLUMNS,
    WORKSPACE_COLUMNS,
    CalendarProposalRow,
    CalendarSourceRow,
    CalendarWorkspaceRow,
    query_calendar_proposals,
    query_calendar_sources,
    query_calendar_workspace,
    row_to_proposal,
    row_to_source,
    row_to_workspace,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 18, 10, 0, 0, tzinfo=UTC)
_SOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
_INSTANCE_ID = UUID("20000000-0000-0000-0000-000000000002")
_EVENT_ID = UUID("30000000-0000-0000-0000-000000000003")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(d: dict) -> MagicMock:
    """Wrap a dict in a MagicMock that supports subscript access."""
    m = MagicMock()
    m.__getitem__ = lambda self, k: d[k]
    return m


def _source_dict(**overrides) -> dict:
    base = {
        "source_id": _SOURCE_ID,
        "source_key": "primary",
        "source_kind": "google",
        "lane": "user",
        "provider": "google",
        "calendar_id": "alice@example.com",
        "butler_name": "assistant",
        "display_name": "Alice's Calendar",
        "writable": True,
        "source_metadata": None,
        "cursor_name": "main",
        "last_synced_at": _NOW - timedelta(minutes=5),
        "last_success_at": _NOW - timedelta(minutes=5),
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }
    base.update(overrides)
    return base


def _workspace_dict(**overrides) -> dict:
    base = {
        "instance_id": _INSTANCE_ID,
        "origin_instance_ref": "evt_abc_20260618",
        "instance_timezone": "America/New_York",
        "instance_starts_at": _NOW,
        "instance_ends_at": _NOW + timedelta(hours=1),
        "instance_status": None,
        "instance_metadata": None,
        "event_id": _EVENT_ID,
        "origin_ref": "evt_abc",
        "title": "Team standup",
        "description": "Daily sync",
        "location": None,
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": None,
        "source_id": _SOURCE_ID,
        "source_key": "primary",
        "source_kind": "google",
        "lane": "user",
        "provider": "google",
        "calendar_id": "alice@example.com",
        "butler_name": "assistant",
        "display_name": "Alice's Calendar",
        "writable": True,
        "source_metadata": None,
        "cursor_name": "main",
        "last_synced_at": _NOW - timedelta(minutes=5),
        "last_success_at": _NOW - timedelta(minutes=5),
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }
    base.update(overrides)
    return base


def _make_db(fan_out_result: dict) -> MagicMock:
    db = MagicMock()
    db.fan_out = AsyncMock(return_value=fan_out_result)
    db.butlers_with_module = MagicMock(return_value=["assistant"])
    return db


# ---------------------------------------------------------------------------
# Version marker
# ---------------------------------------------------------------------------


def test_version_marker_is_calendar_workspace_v1():
    assert READ_MODEL_VERSION == "calendar_workspace_v1"


# ---------------------------------------------------------------------------
# Column constants
# ---------------------------------------------------------------------------


def test_source_columns_is_non_empty_string():
    assert isinstance(SOURCE_COLUMNS, str) and len(SOURCE_COLUMNS) > 0


def test_source_columns_has_expected_identifiers():
    for col in (
        "source_id",
        "source_key",
        "source_kind",
        "lane",
        "butler_name",
        "last_synced_at",
        "full_sync_required",
    ):
        assert col in SOURCE_COLUMNS, f"Expected '{col}' in SOURCE_COLUMNS"


def test_workspace_columns_is_non_empty_string():
    assert isinstance(WORKSPACE_COLUMNS, str) and len(WORKSPACE_COLUMNS) > 0


def test_workspace_columns_has_instance_identifiers():
    for col in ("instance_id", "instance_starts_at", "instance_ends_at", "instance_status"):
        assert col in WORKSPACE_COLUMNS, f"Expected '{col}' in WORKSPACE_COLUMNS"


def test_workspace_columns_has_event_identifiers():
    for col in ("event_id", "origin_ref", "title", "all_day", "recurrence_rule"):
        assert col in WORKSPACE_COLUMNS, f"Expected '{col}' in WORKSPACE_COLUMNS"


def test_workspace_columns_has_source_identifiers():
    for col in ("source_id", "source_key", "lane", "full_sync_required"):
        assert col in WORKSPACE_COLUMNS, f"Expected '{col}' in WORKSPACE_COLUMNS"


# ---------------------------------------------------------------------------
# row_to_source
# ---------------------------------------------------------------------------


def test_row_to_source_maps_all_fields():
    row = _make_record(_source_dict())
    dto = row_to_source(row, db_butler="assistant")

    assert isinstance(dto, CalendarSourceRow)
    assert dto.source_id == _SOURCE_ID
    assert dto.source_key == "primary"
    assert dto.source_kind == "google"
    assert dto.lane == "user"
    assert dto.provider == "google"
    assert dto.calendar_id == "alice@example.com"
    assert dto.butler_name == "assistant"
    assert dto.display_name == "Alice's Calendar"
    assert dto.writable is True
    assert dto.cursor_name == "main"
    assert dto.full_sync_required is False
    assert dto.db_butler == "assistant"


def test_row_to_source_butler_name_null_falls_back_to_db_butler():
    """When row butler_name is NULL, db_butler is used as the fallback."""
    row = _make_record(_source_dict(butler_name=None))
    dto = row_to_source(row, db_butler="secretary")
    assert dto.butler_name == "secretary"
    assert dto.db_butler == "secretary"


def test_row_to_source_butler_name_non_null_kept():
    """When row butler_name has a value, it is kept (not overwritten by db_butler)."""
    row = _make_record(_source_dict(butler_name="assistant"))
    dto = row_to_source(row, db_butler="secretary")
    assert dto.butler_name == "assistant"
    assert dto.db_butler == "secretary"


def test_row_to_source_false_writable_is_false():
    row = _make_record(_source_dict(writable=False))
    dto = row_to_source(row, db_butler="assistant")
    assert dto.writable is False


def test_row_to_source_null_writable_becomes_false():
    row = _make_record(_source_dict(writable=None))
    dto = row_to_source(row, db_butler="assistant")
    assert dto.writable is False


def test_row_to_source_null_full_sync_required_becomes_false():
    row = _make_record(_source_dict(full_sync_required=None))
    dto = row_to_source(row, db_butler="assistant")
    assert dto.full_sync_required is False


def test_row_to_source_none_cursor_fields():
    row = _make_record(
        _source_dict(
            cursor_name=None,
            last_synced_at=None,
            last_success_at=None,
            last_error_at=None,
            last_error=None,
        )
    )
    dto = row_to_source(row, db_butler="assistant")
    assert dto.cursor_name is None
    assert dto.last_synced_at is None
    assert dto.last_error is None


# ---------------------------------------------------------------------------
# row_to_workspace
# ---------------------------------------------------------------------------


def test_row_to_workspace_maps_all_fields():
    row = _make_record(_workspace_dict())
    dto = row_to_workspace(row, db_butler="assistant")

    assert isinstance(dto, CalendarWorkspaceRow)
    assert dto.instance_id == _INSTANCE_ID
    assert dto.event_id == _EVENT_ID
    assert dto.title == "Team standup"
    assert dto.all_day is False
    assert dto.lane == "user"
    assert dto.db_butler == "assistant"
    assert dto.instance_starts_at == _NOW
    assert dto.instance_ends_at == _NOW + timedelta(hours=1)


def test_row_to_workspace_null_all_day_becomes_false():
    row = _make_record(_workspace_dict(all_day=None))
    dto = row_to_workspace(row, db_butler="assistant")
    assert dto.all_day is False


def test_row_to_workspace_null_optional_fields():
    row = _make_record(
        _workspace_dict(
            location=None,
            recurrence_rule=None,
            description=None,
            instance_metadata=None,
            event_metadata=None,
        )
    )
    dto = row_to_workspace(row, db_butler="assistant")
    assert dto.location is None
    assert dto.recurrence_rule is None
    assert dto.description is None


# ---------------------------------------------------------------------------
# query_calendar_sources
# ---------------------------------------------------------------------------


async def test_query_calendar_sources_returns_typed_dtos():
    db = _make_db({"assistant": [_make_record(_source_dict())]})

    result = await query_calendar_sources(db)

    assert len(result) == 1
    assert isinstance(result[0], CalendarSourceRow)
    assert result[0].source_key == "primary"


async def test_query_calendar_sources_uses_butlers_with_module_when_no_butlers_arg():
    db = _make_db({"assistant": [_make_record(_source_dict())]})

    await query_calendar_sources(db)

    db.butlers_with_module.assert_called_once_with("calendar")
    _, kwargs = db.fan_out.call_args
    assert kwargs.get("butler_names") == ["assistant"]


async def test_query_calendar_sources_uses_explicit_butlers_arg():
    db = _make_db({"secretary": [_make_record(_source_dict())]})

    await query_calendar_sources(db, butlers=["secretary"])

    _, kwargs = db.fan_out.call_args
    assert kwargs.get("butler_names") == ["secretary"]
    # should NOT call butlers_with_module
    db.butlers_with_module.assert_not_called()


async def test_query_calendar_sources_db_butler_set_on_dto():
    db = _make_db({"secretary": [_make_record(_source_dict())]})

    result = await query_calendar_sources(db)

    assert result[0].db_butler == "secretary"


async def test_query_calendar_sources_multiple_butlers_flattened():
    db = _make_db(
        {
            "assistant": [_make_record(_source_dict())],
            "secretary": [_make_record(_source_dict(source_key="work"))],
        }
    )

    result = await query_calendar_sources(db)

    assert len(result) == 2
    keys = {r.source_key for r in result}
    assert keys == {"primary", "work"}


# ---------------------------------------------------------------------------
# query_calendar_workspace
# ---------------------------------------------------------------------------


async def test_query_calendar_workspace_returns_typed_dtos():
    db = _make_db({"assistant": [_make_record(_workspace_dict())]})
    start = _NOW - timedelta(days=1)
    end = _NOW + timedelta(days=1)

    result = await query_calendar_workspace(db, view="user", start=start, end=end)

    assert len(result) == 1
    assert isinstance(result[0], CalendarWorkspaceRow)
    assert result[0].title == "Team standup"


async def test_query_calendar_workspace_uses_butlers_with_module_when_no_butlers_arg():
    db = _make_db({"assistant": [_make_record(_workspace_dict())]})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end)

    db.butlers_with_module.assert_called_once_with("calendar")


async def test_query_calendar_workspace_uses_explicit_butlers_arg():
    db = _make_db({"secretary": [_make_record(_workspace_dict())]})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="butler", start=start, end=end, butlers=["secretary"])

    _, kwargs = db.fan_out.call_args
    assert kwargs.get("butler_names") == ["secretary"]
    db.butlers_with_module.assert_not_called()


async def test_query_calendar_workspace_sql_has_time_range_filter():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end)

    sql = db.fan_out.call_args[0][0]
    assert "starts_at" in sql
    assert "ends_at" in sql
    # start and end are passed as positional args
    args = db.fan_out.call_args[0][1]
    assert end in args
    assert start in args


async def test_query_calendar_workspace_db_butler_set_on_dto():
    db = _make_db({"secretary": [_make_record(_workspace_dict())]})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    result = await query_calendar_workspace(db, view="user", start=start, end=end)

    assert result[0].db_butler == "secretary"


async def test_query_calendar_workspace_multiple_butlers_flattened():
    db = _make_db(
        {
            "assistant": [_make_record(_workspace_dict())],
            "secretary": [_make_record(_workspace_dict(title="1:1"))],
        }
    )
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    result = await query_calendar_workspace(db, view="user", start=start, end=end)

    assert len(result) == 2
    titles = {r.title for r in result}
    assert titles == {"Team standup", "1:1"}


async def test_query_calendar_workspace_sql_has_lateral_join():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end)

    sql = db.fan_out.call_args[0][0]
    assert "LATERAL" in sql
    assert "calendar_event_instances" in sql
    assert "calendar_events" in sql


# ---------------------------------------------------------------------------
# query_calendar_workspace — server-side facets + keyset pagination (bu-xr1i95)
# ---------------------------------------------------------------------------


async def test_query_calendar_workspace_status_facet_adds_predicate():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end, status="paused")

    sql = db.fan_out.call_args[0][0]
    args = db.fan_out.call_args[0][1]
    # Status is computed over the instance/event status (server-side CASE expr).
    assert "i.status" in sql
    assert "paused" in args


async def test_query_calendar_workspace_source_type_facet_adds_predicate():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(
        db, view="user", start=start, end=end, source_type="provider_event"
    )

    sql = db.fan_out.call_args[0][0]
    args = db.fan_out.call_args[0][1]
    # source_type is computed over the source_kind / event metadata.
    assert "s.source_kind" in sql
    assert "provider_event" in args


async def test_query_calendar_workspace_editable_facet_adds_writable_predicate():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end, editable=True)

    sql = db.fan_out.call_args[0][0]
    args = db.fan_out.call_args[0][1]
    assert "COALESCE(s.writable, false) =" in sql
    assert True in args


async def test_query_calendar_workspace_facets_and_together():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(
        db,
        view="user",
        start=start,
        end=end,
        status="active",
        source_type="provider_event",
        editable=False,
    )

    args = db.fan_out.call_args[0][1]
    # All three facet values bind as positional params (combined with AND).
    assert "active" in args
    assert "provider_event" in args
    assert False in args


async def test_query_calendar_workspace_cursor_adds_keyset_predicate():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)
    cursor_id = UUID("50000000-0000-0000-0000-000000000005")

    await query_calendar_workspace(db, view="user", start=start, end=end, cursor=(_NOW, cursor_id))

    sql = db.fan_out.call_args[0][0]
    args = db.fan_out.call_args[0][1]
    assert "(i.starts_at, i.id) >" in sql
    assert _NOW in args
    assert cursor_id in args


async def test_query_calendar_workspace_limit_adds_limit_clause():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end, limit=5)

    sql = db.fan_out.call_args[0][0]
    args = db.fan_out.call_args[0][1]
    assert "LIMIT $" in sql
    assert 5 in args


async def test_query_calendar_workspace_no_facets_preserves_base_query():
    """Omitting all facets/cursor/limit leaves the prior behavior unchanged."""
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_workspace(db, view="user", start=start, end=end)

    sql = db.fan_out.call_args[0][0]
    assert "(i.starts_at, i.id) >" not in sql
    assert "LIMIT $" not in sql  # the LATERAL subquery uses a literal LIMIT 1
    # ``s.writable`` appears in the SELECT projection, but no editable predicate.
    assert "COALESCE(s.writable, false) =" not in sql


# ---------------------------------------------------------------------------
# query_calendar_proposals — proposals lane projection (bu-dn65mb)
# ---------------------------------------------------------------------------


def _proposal_dict(**overrides) -> dict:
    base = {
        "proposal_id": UUID("40000000-0000-0000-0000-000000000004"),
        "butler_name": "assistant",
        "title": "Dentist appointment",
        "start_at": _NOW,
        "end_at": _NOW + timedelta(hours=1),
        "description": "From your inbox",
        "location": "123 Main St",
        "timezone": "UTC",
        "source_event_id": "ingest-evt-1",
        "source_snippet": "confirmed for 10am",
        "confidence": 0.9,
        "entity_ids": [],
        "status": "pending",
        "accepted_event_id": None,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


async def test_query_calendar_proposals_returns_typed_dtos():
    db = _make_db({"assistant": [_make_record(_proposal_dict())]})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    rows = await query_calendar_proposals(db, start=start, end=end)

    assert len(rows) == 1
    assert isinstance(rows[0], CalendarProposalRow)
    assert rows[0].title == "Dentist appointment"
    assert rows[0].db_butler == "assistant"


async def test_query_calendar_proposals_sql_filters_pending_and_range():
    db = _make_db({})
    start = _NOW - timedelta(hours=1)
    end = _NOW + timedelta(hours=1)

    await query_calendar_proposals(db, start=start, end=end)

    sql = db.fan_out.call_args[0][0]
    args = db.fan_out.call_args[0][1]
    assert "calendar_event_proposals" in sql
    assert "p.status = 'pending'" in sql
    assert "p.start_at >= $1" in sql
    assert "p.start_at < $2" in sql
    assert args == (start, end)


async def test_query_calendar_proposals_uses_butlers_with_module_when_no_butlers_arg():
    db = _make_db({})
    await query_calendar_proposals(db, start=_NOW, end=_NOW + timedelta(hours=1))
    db.butlers_with_module.assert_called_once_with("calendar")


async def test_query_calendar_proposals_fail_open_on_fan_out_error():
    db = _make_db({})
    db.fan_out = AsyncMock(side_effect=RuntimeError("relation does not exist"))

    rows = await query_calendar_proposals(db, start=_NOW, end=_NOW + timedelta(hours=1))

    assert rows == []


def test_row_to_proposal_maps_columns():
    record = _make_record(_proposal_dict(butler_name=None))
    dto = row_to_proposal(record, db_butler="assistant")
    # butler_name falls back to the db_butler schema name when NULL.
    assert dto.butler_name == "assistant"
    assert dto.source_event_id == "ingest-evt-1"
    assert dto.confidence == 0.9


def test_proposal_columns_non_empty():
    assert "calendar_event_proposals" not in PROPOSAL_COLUMNS  # columns only, no FROM
    assert "p.source_event_id" in PROPOSAL_COLUMNS
    assert "p.confidence" in PROPOSAL_COLUMNS
