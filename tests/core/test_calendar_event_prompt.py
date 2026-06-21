"""Tests for calendar_create_butler_event prompt construction.

Problem 1 (bu-0g76b): when the caller does not supply an explicit action, the
default "Run butler event" string is stored as the scheduled_task prompt.  The
firing session then has no context and improvises — causing off-cron freelancing.

Fix: when action is the default (or empty), calendar_create_butler_event builds
a descriptive prompt from the event title and local start time, e.g.
  "Scheduled event: Pay bills at 09:00 UTC."

These tests assert that the constructed prompt (a) contains the event title and
(b) does NOT contain the bare "Run butler event" sentinel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from butlers.modules.calendar import _CALENDAR_EVENT_DEFAULT_ACTION, CalendarModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pool mock — returns just enough for the tool to reach _schedule_create
# ---------------------------------------------------------------------------


class _ScheduleCreatePool:
    """Minimal pool double that captures prompt passed to _schedule_create."""

    def __init__(self) -> None:
        self._task_id = uuid.uuid4()
        # Capture every query/args pair that goes to the INSERT for scheduled_tasks
        self.schedule_create_calls: list[dict] = []

    async def fetchval(self, query: str, *args) -> object:
        # _table_exists check for "reminders" and general existence checks
        if "information_schema.tables" in query or "to_regclass" in query:
            # scheduled_tasks exists; reminders does NOT (forces scheduled_task path)
            if "reminders" in (args[0] if args else ""):
                return None
            return "scheduled_tasks"
        return None

    async def fetchrow(self, query: str, *args) -> dict | None:
        q = query.strip().upper()
        # Workspace mutation action_log check (idempotency key lookup)
        if "FROM calendar_action_log" in q or "CALENDAR_ACTION_LOG" in q:
            return None
        # INSERT INTO calendar_action_log … RETURNING
        if "INSERT INTO calendar_action_log" in q or "RETURNING" in q:
            return {"id": uuid.uuid4()}
        # calendar_sources ensure row
        if "CALENDAR_SOURCES" in q:
            return {"id": uuid.uuid4()}
        # _schedule_create inner fetchrow (INSERT INTO scheduled_tasks RETURNING id)
        if "SCHEDULED_TASKS" in q and "RETURNING" in q:
            return {"id": self._task_id}
        return {"id": uuid.uuid4()}

    async def fetch(self, query: str, *args) -> list:
        # seasonal_periods check, event_chains, etc.
        return []

    async def execute(self, query: str, *args) -> str:
        return "OK"

    def acquire(self):
        return _FakePoolContext(self)


class _FakePoolContext:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self._pool

    async def __aexit__(self, *a):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_event_module() -> CalendarModule:
    """Return a CalendarModule wired with a _ScheduleCreatePool."""
    mod = CalendarModule()
    mod._butler_name = "finance"
    mod._config = MagicMock()
    mod._config.timezone = "UTC"
    pool = _ScheduleCreatePool()
    db = MagicMock()
    db.pool = pool
    mod._db = db
    return mod


async def _register_and_get_tool(mod: CalendarModule) -> object:
    """Register tools on mod and return the calendar_create_butler_event callable."""
    mcp_tools: dict = {}

    class _StubMCP:
        def tool(self):
            def deco(fn):
                mcp_tools[fn.__name__] = fn
                return fn

            return deco

    stub = _StubMCP()
    # db passed to register_tools must have a .pool attribute; mod._db.pool is the
    # _ScheduleCreatePool created by _make_butler_event_module().
    pool = mod._db.pool
    db_with_pool = MagicMock()
    db_with_pool.pool = pool
    db_with_pool.db_name = "butlers"
    db_with_pool.db_schema = "finance"
    await mod.register_tools(
        mcp=stub,
        config={"provider": "google", "calendar_id": "cal-id"},
        db=db_with_pool,
        butler_name="finance",
    )
    return mcp_tools.get("calendar_create_butler_event")


# ---------------------------------------------------------------------------
# Tests: prompt carries event title
# ---------------------------------------------------------------------------


class TestCalendarEventPromptCarriesIntent:
    """calendar_create_butler_event must store a descriptive prompt, not the
    generic "Run butler event" sentinel, when no explicit action is given.
    """

    async def test_default_action_prompt_contains_title(self, monkeypatch) -> None:
        """Prompt passed to _schedule_create includes the event title."""
        captured: list[dict] = []

        async def _fake_schedule_create(pool, name, cron, prompt, **kwargs):
            captured.append({"prompt": prompt, "display_title": kwargs.get("display_title")})
            return uuid.uuid4()

        monkeypatch.setattr("butlers.modules.calendar._schedule_create", _fake_schedule_create)

        mod = _make_butler_event_module()
        tool = await _register_and_get_tool(mod)
        assert tool is not None, "calendar_create_butler_event not registered"

        start = datetime(2026, 6, 20, 9, 15, tzinfo=UTC)
        await tool(
            butler_name="finance",
            title="Pay bills",
            start_at=start,
            cron="15 9 * * *",
            # action omitted → default "Run butler event"
        )

        assert len(captured) == 1, "Expected exactly one _schedule_create call"
        prompt = captured[0]["prompt"]
        assert "Pay bills" in prompt, f"Event title missing from prompt: {prompt!r}"
        assert _CALENDAR_EVENT_DEFAULT_ACTION not in prompt, (
            f"Generic default action must not appear in prompt: {prompt!r}"
        )

    async def test_default_action_prompt_contains_time(self, monkeypatch) -> None:
        """Prompt includes the local start time so the session knows when the event is."""
        captured: list[dict] = []

        async def _fake_schedule_create(pool, name, cron, prompt, **kwargs):
            captured.append({"prompt": prompt})
            return uuid.uuid4()

        monkeypatch.setattr("butlers.modules.calendar._schedule_create", _fake_schedule_create)

        mod = _make_butler_event_module()
        tool = await _register_and_get_tool(mod)

        start = datetime(2026, 6, 20, 9, 15, tzinfo=UTC)
        await tool(
            butler_name="finance",
            title="Monthly review",
            start_at=start,
            cron="15 9 20 * *",
        )

        prompt = captured[0]["prompt"]
        # The time component ("09:15") must appear in the prompt
        assert "09:15" in prompt, f"Start time missing from prompt: {prompt!r}"

    async def test_explicit_action_is_preserved(self, monkeypatch) -> None:
        """When an explicit, non-default action is supplied it is stored verbatim."""
        captured: list[dict] = []

        async def _fake_schedule_create(pool, name, cron, prompt, **kwargs):
            captured.append({"prompt": prompt})
            return uuid.uuid4()

        monkeypatch.setattr("butlers.modules.calendar._schedule_create", _fake_schedule_create)

        mod = _make_butler_event_module()
        tool = await _register_and_get_tool(mod)

        start = datetime(2026, 6, 20, 9, 15, tzinfo=UTC)
        explicit = "Run the monthly reconciliation and notify owner of results."
        await tool(
            butler_name="finance",
            title="Monthly reconciliation",
            start_at=start,
            cron="15 9 20 * *",
            action=explicit,
        )

        prompt = captured[0]["prompt"]
        assert prompt == explicit, f"Explicit action not preserved verbatim.  Got: {prompt!r}"

    async def test_notes_appended_when_present(self, monkeypatch) -> None:
        """Notes from action_args are appended to the built prompt."""
        captured: list[dict] = []

        async def _fake_schedule_create(pool, name, cron, prompt, **kwargs):
            captured.append({"prompt": prompt})
            return uuid.uuid4()

        monkeypatch.setattr("butlers.modules.calendar._schedule_create", _fake_schedule_create)

        mod = _make_butler_event_module()
        tool = await _register_and_get_tool(mod)

        start = datetime(2026, 6, 20, 9, 15, tzinfo=UTC)
        await tool(
            butler_name="finance",
            title="Pay bills",
            start_at=start,
            cron="15 9 * * *",
            action_args={"notes": "Focus on overdue subscriptions only."},
        )

        prompt = captured[0]["prompt"]
        assert "overdue subscriptions" in prompt, f"Notes not appended to prompt: {prompt!r}"

    async def test_empty_action_treated_as_default(self, monkeypatch) -> None:
        """An empty-string action is treated the same as the default sentinel."""
        captured: list[dict] = []

        async def _fake_schedule_create(pool, name, cron, prompt, **kwargs):
            captured.append({"prompt": prompt})
            return uuid.uuid4()

        monkeypatch.setattr("butlers.modules.calendar._schedule_create", _fake_schedule_create)

        mod = _make_butler_event_module()
        tool = await _register_and_get_tool(mod)

        start = datetime(2026, 6, 20, 9, 15, tzinfo=UTC)
        await tool(
            butler_name="finance",
            title="Daily briefing",
            start_at=start,
            cron="0 8 * * *",
            action="   ",  # whitespace-only — should be treated as default
        )

        prompt = captured[0]["prompt"]
        assert "Daily briefing" in prompt, f"Title missing from prompt: {prompt!r}"
