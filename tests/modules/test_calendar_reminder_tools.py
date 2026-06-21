"""Unit tests for reminder_create, reminder_list, reminder_dismiss MCP tools.

These tools store reminders as native calendar_events rows with
source_kind='internal_reminders'.  All tests use DB pool mocks to stay
unit-level (no live DB required).

[bu-42rpy]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.calendar import SOURCE_KIND_INTERNAL_REMINDERS, CalendarModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _StubMCP:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def _make_db(*, pool=None) -> SimpleNamespace:
    """Build a minimal DB stub with an optionally supplied pool."""
    return SimpleNamespace(db_name="butlers", schema="relationship", pool=pool)


def _make_pool(
    *,
    fetchrow_result=None,
    fetchrow_side_effect=None,
    fetch_result=None,
) -> MagicMock:
    """Build an asyncpg-like pool mock."""
    pool = MagicMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    pool.fetch = AsyncMock(return_value=fetch_result or [])
    pool.execute = AsyncMock(return_value=None)
    pool.executemany = AsyncMock(return_value=None)
    return pool


async def _make_module(
    *,
    butler_name: str = "relationship",
    pool: MagicMock | None = None,
) -> tuple[_StubMCP, CalendarModule]:
    """Register a CalendarModule with a _StubMCP, bypassing table checks."""
    mod = CalendarModule()
    mcp = _StubMCP()
    await mod.register_tools(
        mcp=mcp, config={"provider": "google"}, db=None, butler_name="test-butler"
    )
    mod._butler_name = butler_name
    if pool is not None:
        mod._db = _make_db(pool=pool)
        # Bypass projection table availability checks.
        mod._projection_tables_available_cache = True
    return mcp, mod


_DUE_AT = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
_ENDS_AT = _DUE_AT + timedelta(minutes=15)
_EVENT_ID = uuid.uuid4()
_SOURCE_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestReminderToolsRegistered:
    async def test_reminder_tools_are_registered(self):
        mod = CalendarModule()
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp, config={"provider": "google"}, db=None, butler_name="test-butler"
        )
        assert "reminder_create" in mcp.tools
        assert "reminder_list" in mcp.tools
        assert "reminder_dismiss" in mcp.tools


# ---------------------------------------------------------------------------
# reminder_create: input validation
# ---------------------------------------------------------------------------


class TestReminderCreateValidation:
    async def test_blank_title_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="title"):
            await mcp.tools["reminder_create"](title="   ", due_at=_DUE_AT)

    async def test_naive_due_at_raises(self):
        mcp, _ = await _make_module()
        naive = datetime(2026, 5, 1, 9, 0)
        with pytest.raises(ValueError, match="due_at must be timezone-aware"):
            await mcp.tools["reminder_create"](title="Call Mom", due_at=naive)

    async def test_ends_at_before_due_at_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="ends_at must be after due_at"):
            await mcp.tools["reminder_create"](
                title="Call Mom",
                due_at=_DUE_AT,
                ends_at=_DUE_AT - timedelta(minutes=5),
            )

    async def test_invalid_recurrence_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="Unsupported recurrence"):
            await mcp.tools["reminder_create"](
                title="Call Mom",
                due_at=_DUE_AT,
                recurrence="biweekly",
            )

    async def test_invalid_entity_id_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="Invalid entity_id"):
            await mcp.tools["reminder_create"](
                title="Call Mom",
                due_at=_DUE_AT,
                entity_ids=["not-a-uuid"],
            )


# ---------------------------------------------------------------------------
# reminder_create: happy-path behavior
# ---------------------------------------------------------------------------


def _source_row() -> dict:
    return {"id": _SOURCE_ID}


def _event_row_dict(
    *,
    title: str = "Call Mom",
    recurrence_rule: str | None = None,
    ends_at: datetime = _ENDS_AT,
) -> dict:
    return {
        "id": _EVENT_ID,
        "title": title,
        "body": None,
        "starts_at": _DUE_AT,
        "ends_at": ends_at,
        "status": "confirmed",
        "recurrence_rule": recurrence_rule,
        "source_butler": "relationship",
        "source_session_id": None,
    }


class TestReminderCreateBehavior:
    async def test_default_ends_at_is_15_minutes_after_due(self):
        """reminder_create uses due_at + 15min when ends_at is omitted."""
        pool = _make_pool(fetchrow_side_effect=[_source_row(), _event_row_dict()])
        mcp, mod = await _make_module(pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            result = await mcp.tools["reminder_create"](title="Call Mom", due_at=_DUE_AT)

        assert result["status"] == "created"
        assert result["starts_at"] == _DUE_AT.isoformat()
        assert result["ends_at"] == _ENDS_AT.isoformat()
        assert result["recurrence_rule"] is None
        assert result["source_butler"] == "relationship"

    async def test_explicit_ends_at_honored(self):
        explicit_ends = _DUE_AT + timedelta(hours=1)
        pool = _make_pool(
            fetchrow_side_effect=[_source_row(), _event_row_dict(ends_at=explicit_ends)]
        )
        mcp, mod = await _make_module(pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            result = await mcp.tools["reminder_create"](
                title="Call Mom", due_at=_DUE_AT, ends_at=explicit_ends
            )

        assert result["ends_at"] == explicit_ends.isoformat()

    async def test_yearly_recurrence_produces_rrule(self):
        pool = _make_pool(
            fetchrow_side_effect=[
                _source_row(),
                _event_row_dict(title="Mom birthday", recurrence_rule="RRULE:FREQ=YEARLY"),
            ]
        )
        mcp, mod = await _make_module(pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            result = await mcp.tools["reminder_create"](
                title="Mom birthday", due_at=_DUE_AT, recurrence="yearly"
            )

        assert result["recurrence_rule"] == "RRULE:FREQ=YEARLY"

    async def test_monthly_recurrence_produces_rrule(self):
        pool = _make_pool(
            fetchrow_side_effect=[
                _source_row(),
                _event_row_dict(title="Rent due", recurrence_rule="RRULE:FREQ=MONTHLY"),
            ]
        )
        mcp, mod = await _make_module(pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            result = await mcp.tools["reminder_create"](
                title="Rent due", due_at=_DUE_AT, recurrence="monthly"
            )

        assert result["recurrence_rule"] == "RRULE:FREQ=MONTHLY"

    async def test_entity_ids_linked(self):
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_side_effect=[_source_row(), _event_row_dict()])
        mcp, mod = await _make_module(pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            result = await mcp.tools["reminder_create"](
                title="Call Mom", due_at=_DUE_AT, entity_ids=[str(entity_id)]
            )

        # executemany must be called to insert the junction row.
        pool.executemany.assert_called_once()
        call_args = pool.executemany.call_args
        # Second positional arg is list of (event_id, entity_id) tuples.
        rows = call_args[0][1]
        assert len(rows) == 1
        assert rows[0][1] == entity_id
        assert str(entity_id) in result["entity_ids"]

    async def test_no_entity_ids_skips_junction_insert(self):
        pool = _make_pool(
            fetchrow_side_effect=[_source_row(), _event_row_dict(title="Go for a run")]
        )
        mcp, mod = await _make_module(pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            await mcp.tools["reminder_create"](title="Go for a run", due_at=_DUE_AT)

        pool.executemany.assert_not_called()

    async def test_source_butler_passed_to_insert(self):
        """INSERT must receive the module's butler name."""
        pool = _make_pool(fetchrow_side_effect=[_source_row(), _event_row_dict()])
        mcp, mod = await _make_module(butler_name="relationship", pool=pool)

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value="sess-abc",
        ):
            result = await mcp.tools["reminder_create"](title="Call Mom", due_at=_DUE_AT)

        # The last fetchrow call is the INSERT; verify "relationship" appears in its args.
        insert_call = pool.fetchrow.call_args_list[-1]
        positional_args = insert_call[0]
        assert "relationship" in positional_args
        assert result["source_butler"] == "relationship"


# ---------------------------------------------------------------------------
# reminder_list: behavior
# ---------------------------------------------------------------------------


class TestReminderList:
    async def test_no_db_pool_returns_empty(self):
        mcp, mod = await _make_module()
        mod._db = _make_db(pool=None)
        result = await mcp.tools["reminder_list"]()
        assert result["count"] == 0
        assert result["reminders"] == []

    async def test_tables_not_available_returns_empty(self):
        pool = _make_pool()
        mcp, mod = await _make_module(pool=pool)
        # Projection tables unavailable.
        mod._projection_tables_available_cache = False

        result = await mcp.tools["reminder_list"]()
        assert result["count"] == 0
        assert result["reminders"] == []

    async def test_returns_reminders_list(self):
        reminder_row = MagicMock()
        data = {
            "id": _EVENT_ID,
            "title": "Call Mom",
            "body": None,
            "starts_at": _DUE_AT,
            "ends_at": _ENDS_AT,
            "timezone": "UTC",
            "status": "confirmed",
            "recurrence_rule": None,
            "source_butler": "relationship",
            "source_session_id": None,
            "created_at": _DUE_AT,
            "updated_at": _DUE_AT,
        }
        reminder_row.__getitem__ = lambda self, key: data[key]

        pool = _make_pool(
            fetch_result=[reminder_row],  # first fetch: events
        )
        # Second pool.fetch call (entity_map) returns [].
        pool.fetch = AsyncMock(side_effect=[[reminder_row], []])
        mcp, mod = await _make_module(pool=pool)

        result = await mcp.tools["reminder_list"]()
        assert result["count"] == 1
        assert result["reminders"][0]["title"] == "Call Mom"
        assert result["reminders"][0]["entity_ids"] == []

    async def test_invalid_entity_id_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="Invalid entity_id"):
            await mcp.tools["reminder_list"](entity_id="not-a-uuid")


# ---------------------------------------------------------------------------
# reminder_dismiss: behavior
# ---------------------------------------------------------------------------


def _dismiss_event_row(
    *,
    recurrence_rule: str | None = None,
    status: str = "confirmed",
    source_butler: str = "relationship",
    source_kind: str = SOURCE_KIND_INTERNAL_REMINDERS,
) -> MagicMock:
    data = {
        "id": _EVENT_ID,
        "title": "Call Mom",
        "status": status,
        "recurrence_rule": recurrence_rule,
        "source_butler": source_butler,
        "source_kind": source_kind,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


class TestReminderDismiss:
    async def test_blank_event_id_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            await mcp.tools["reminder_dismiss"](event_id="   ")

    async def test_invalid_uuid_raises(self):
        mcp, _ = await _make_module()
        with pytest.raises(ValueError, match="must be a valid UUID"):
            await mcp.tools["reminder_dismiss"](event_id="not-a-uuid")

    async def test_missing_event_raises(self):
        pool = _make_pool(fetchrow_result=None)
        mcp, mod = await _make_module(pool=pool)
        with pytest.raises(ValueError, match="not found"):
            await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

    async def test_wrong_source_kind_raises(self):
        row = _dismiss_event_row(source_kind="provider_event")
        pool = _make_pool(fetchrow_result=row)
        mcp, mod = await _make_module(pool=pool)
        with pytest.raises(ValueError, match="not a reminder"):
            await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

    async def test_wrong_butler_raises(self):
        row = _dismiss_event_row(source_butler="health")
        pool = _make_pool(fetchrow_result=row)
        mcp, mod = await _make_module(pool=pool)
        with pytest.raises(ValueError, match="belongs to butler"):
            await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

    async def test_one_time_reminder_cancelled(self):
        """Dismissing a one-time reminder sets status='cancelled' on the event row."""
        row = _dismiss_event_row(recurrence_rule=None)
        pool = _make_pool(fetchrow_result=row)
        mcp, mod = await _make_module(pool=pool)

        result = await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

        assert result["status"] == "dismissed"
        assert result["recurrence"] == "one_time"
        pool.execute.assert_called_once()

    async def test_already_cancelled_one_time_is_noop(self):
        row = _dismiss_event_row(recurrence_rule=None, status="cancelled")
        pool = _make_pool(fetchrow_result=row)
        mcp, mod = await _make_module(pool=pool)

        result = await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

        assert result["status"] == "already_dismissed"
        pool.execute.assert_not_called()

    async def test_recurring_reminder_cancels_earliest_instance(self):
        """Dismissing a recurring reminder cancels the earliest confirmed instance."""
        event_row = _dismiss_event_row(recurrence_rule="RRULE:FREQ=YEARLY")
        instance_id = uuid.uuid4()
        instance_data = {"id": instance_id, "starts_at": _DUE_AT}
        instance_row = MagicMock()
        instance_row.__getitem__ = lambda self, key: instance_data[key]

        pool = _make_pool(fetchrow_side_effect=[event_row, instance_row])
        mcp, mod = await _make_module(pool=pool)

        result = await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

        assert result["status"] == "instance_dismissed"
        assert result["recurrence"] == "recurring"
        assert result["dismissed_instance_starts_at"] == _DUE_AT.isoformat()
        pool.execute.assert_called_once()

    async def test_recurring_reminder_no_instances_returns_graceful_message(self):
        """No upcoming instances → graceful message, series remains active."""
        event_row = _dismiss_event_row(recurrence_rule="RRULE:FREQ=MONTHLY")
        pool = _make_pool(fetchrow_side_effect=[event_row, None])
        mcp, mod = await _make_module(pool=pool)

        result = await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

        assert result["status"] == "no_active_instance"
        assert result["recurrence"] == "recurring"
        pool.execute.assert_not_called()

    async def test_dismiss_recurring_uses_confirmed_status_filter(self):
        """The recurring-dismiss instance query MUST target status = 'confirmed'.

        Regression guard: if the WHERE clause were relaxed to
        ``status != 'cancelled'``, a tentative/superseded instance could be
        selected and cancelled. The instance selection happens in the DB, so a
        mock pool cannot replay the filter — we pin the SQL shape sent to
        ``pool.fetchrow`` for the instance lookup (the 2nd fetchrow call on a
        recurring dismiss).
        """
        event_row = _dismiss_event_row(recurrence_rule="RRULE:FREQ=YEARLY")
        instance_id = uuid.uuid4()
        instance_data = {"id": instance_id, "starts_at": _DUE_AT}
        instance_row = MagicMock()
        instance_row.__getitem__ = lambda self, key: instance_data[key]

        pool = _make_pool(fetchrow_side_effect=[event_row, instance_row])
        mcp, mod = await _make_module(pool=pool)

        await mcp.tools["reminder_dismiss"](event_id=str(_EVENT_ID))

        # The instance lookup is the 2nd fetchrow (1st is the event row).
        instance_query_sql = pool.fetchrow.call_args_list[1][0][0]
        normalized = " ".join(instance_query_sql.split())
        assert "calendar_event_instances" in normalized
        assert "status = 'confirmed'" in normalized
        # A regression to a negated filter must fail this guard.
        assert "!= 'cancelled'" not in normalized
        assert "<> 'cancelled'" not in normalized


# ---------------------------------------------------------------------------
# _insert_reminder_to_calendar_events: source_butler set correctly
# ---------------------------------------------------------------------------
# NOTE: The confirmed-instance targeting on recurring dismiss is guarded
# behaviorally by TestReminderDismiss.test_recurring_reminder_cancels_earliest_instance
# (asserts status == "instance_dismissed" on the earliest confirmed instance).


class TestInsertReminderToCalendarEvents:
    async def test_source_butler_matches_module_butler_name(self):
        """The INSERT must use the module's butler name, not a hardcoded value."""
        pool = _make_pool(
            fetchrow_side_effect=[
                _source_row(),
                _event_row_dict(),
            ]
        )
        mod = CalendarModule()
        mod._butler_name = "health"
        mod._db = _make_db(pool=pool)
        mod._config = mod._coerce_config({"provider": "google"})
        mod._projection_tables_available_cache = True

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value="test-session",
        ):
            event_id, _ = await mod._insert_reminder_to_calendar_events(
                title="Test reminder",
                body=None,
                starts_at=_DUE_AT,
                ends_at=_ENDS_AT,
                timezone="UTC",
                recurrence_rule=None,
                entity_ids=[],
            )

        assert event_id == _EVENT_ID
        # Verify "health" was passed as source_butler in the INSERT call.
        insert_call = pool.fetchrow.call_args_list[-1]
        params = insert_call[0]
        assert "health" in params

    async def test_no_pool_raises_runtime_error(self):
        mod = CalendarModule()
        mod._butler_name = "relationship"
        mod._db = _make_db(pool=None)
        mod._config = mod._coerce_config({"provider": "google"})

        with pytest.raises(RuntimeError, match="Database pool is not available"):
            await mod._insert_reminder_to_calendar_events(
                title="Test",
                body=None,
                starts_at=_DUE_AT,
                ends_at=_ENDS_AT,
                timezone="UTC",
                recurrence_rule=None,
                entity_ids=[],
            )

    async def test_recurring_reminder_inserts_initial_instance(self):
        """A recurring reminder must seed an initial calendar_event_instances row."""
        pool = _make_pool(
            fetchrow_side_effect=[
                _source_row(),
                _event_row_dict(recurrence_rule="RRULE:FREQ=YEARLY"),
            ]
        )
        mod = CalendarModule()
        mod._butler_name = "relationship"
        mod._db = _make_db(pool=pool)
        mod._config = mod._coerce_config({"provider": "google"})
        mod._projection_tables_available_cache = True

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            await mod._insert_reminder_to_calendar_events(
                title="Mom birthday",
                body=None,
                starts_at=_DUE_AT,
                ends_at=_ENDS_AT,
                timezone="UTC",
                recurrence_rule="RRULE:FREQ=YEARLY",
                entity_ids=[],
            )

        # pool.execute should be called once to seed the initial instance.
        pool.execute.assert_called_once()

    async def test_one_time_reminder_does_not_insert_instance(self):
        """A one-time reminder must NOT insert into calendar_event_instances."""
        pool = _make_pool(
            fetchrow_side_effect=[
                _source_row(),
                _event_row_dict(),
            ]
        )
        mod = CalendarModule()
        mod._butler_name = "relationship"
        mod._db = _make_db(pool=pool)
        mod._config = mod._coerce_config({"provider": "google"})
        mod._projection_tables_available_cache = True

        with patch(
            "butlers.core.tool_call_capture.get_current_runtime_session_id",
            return_value=None,
        ):
            await mod._insert_reminder_to_calendar_events(
                title="Call Mom",
                body=None,
                starts_at=_DUE_AT,
                ends_at=_ENDS_AT,
                timezone="UTC",
                recurrence_rule=None,
                entity_ids=[],
            )

        pool.execute.assert_not_called()
