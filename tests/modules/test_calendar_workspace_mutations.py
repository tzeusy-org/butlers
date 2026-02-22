"""Tests for calendar workspace mutation helpers and butler event tools."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = pytest.mark.unit


class _StubMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.db_name = "butler_general"
    db.pool = AsyncMock()
    return db


class TestWorkspaceMutationHelpers:
    async def test_prepare_workspace_mutation_replays_applied_request(self):
        mod = CalendarModule()
        mod._db = _mock_db()

        with (
            patch.object(
                mod,
                "_load_projection_action",
                AsyncMock(side_effect=[None, ("applied", {"status": "created"}, None)]),
            ),
            patch.object(mod, "_record_projection_action", AsyncMock()),
        ):
            _, replay_first = await mod._prepare_workspace_mutation(
                action_type="workspace_user_create",
                request_id="req-1",
                action_payload={"foo": "bar"},
            )
            _, replay_second = await mod._prepare_workspace_mutation(
                action_type="workspace_user_create",
                request_id="req-1",
                action_payload={"foo": "bar"},
            )

        assert replay_first is None
        assert replay_second is not None
        assert replay_second["status"] == "created"
        assert replay_second["idempotent_replay"] is True


class TestButlerEventTools:
    async def test_registers_butler_event_tools(self):
        mod = CalendarModule()
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=_mock_db(),
        )

        assert "calendar_create_butler_event" in mcp.tools
        assert "calendar_update_butler_event" in mcp.tools
        assert "calendar_delete_butler_event" in mcp.tools
        assert "calendar_toggle_butler_event" in mcp.tools

    async def test_create_butler_schedule_event_uses_scheduler_create(self):
        mod = CalendarModule()
        mcp = _StubMCP()
        db = _mock_db()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=db,
        )

        created_task_id = uuid.uuid4()
        with (
            patch.object(
                mod, "_prepare_workspace_mutation", AsyncMock(return_value=("key-1", None))
            ),
            patch.object(mod, "_finalize_workspace_mutation", AsyncMock()),
            patch.object(mod, "_resolve_action_source_id", AsyncMock(return_value=None)),
            patch.object(
                mod, "_refresh_butler_projection", AsyncMock(return_value={"staleness_ms": 0})
            ),
            patch(
                "butlers.modules.calendar._schedule_create", AsyncMock(return_value=created_task_id)
            ) as schedule_create_mock,
        ):
            result = await mcp.tools["calendar_create_butler_event"](
                butler_name="general",
                title="Daily plan",
                start_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
                end_at=datetime(2026, 3, 2, 9, 30, tzinfo=UTC),
                cron="0 9 * * *",
                action="Prepare daily agenda",
                request_id="req-abc",
            )

        assert result["status"] == "created"
        assert result["source_type"] == "scheduled_task"
        assert result["schedule_id"] == str(created_task_id)
        schedule_create_mock.assert_awaited_once()
        create_kwargs = schedule_create_mock.await_args.kwargs
        assert create_kwargs["display_title"] == "Daily plan"
        assert create_kwargs["timezone"] == "UTC"
        assert create_kwargs["calendar_event_id"] is not None

    async def test_create_butler_event_replay_short_circuits(self):
        mod = CalendarModule()
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=_mock_db(),
        )

        replay_payload = {"status": "created", "idempotent_replay": True}
        with (
            patch.object(
                mod,
                "_prepare_workspace_mutation",
                AsyncMock(return_value=("key-replay", replay_payload)),
            ),
            patch("butlers.modules.calendar._schedule_create", AsyncMock()) as schedule_create_mock,
        ):
            result = await mcp.tools["calendar_create_butler_event"](
                butler_name="general",
                title="Daily plan",
                start_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
                end_at=datetime(2026, 3, 2, 9, 30, tzinfo=UTC),
                cron="0 9 * * *",
                action="Prepare daily agenda",
                request_id="req-replay",
            )

        assert result["idempotent_replay"] is True
        schedule_create_mock.assert_not_awaited()

    async def test_delete_butler_event_queues_approval_for_high_impact(self):
        mod = CalendarModule()
        mcp = _StubMCP()
        await mod.register_tools(
            mcp=mcp,
            config={"provider": "google", "calendar_id": "primary"},
            db=_mock_db(),
        )

        async def _enqueue(tool_name: str, tool_args: dict, summary: str) -> str:
            assert tool_name == "calendar_delete_butler_event"
            assert tool_args["_approval_bypass"] is True
            assert "high-impact" in summary
            return "approval-action-1"

        mod.set_approval_enqueuer(_enqueue)

        with (
            patch.object(
                mod, "_prepare_workspace_mutation", AsyncMock(return_value=("key-1", None))
            ),
            patch.object(mod, "_resolve_action_source_id", AsyncMock(return_value=None)),
            patch.object(mod, "_finalize_workspace_mutation", AsyncMock()),
        ):
            result = await mcp.tools["calendar_delete_butler_event"](
                event_id=str(uuid.uuid4()),
                request_id="req-delete-1",
            )

        assert result["status"] == "approval_required"
        assert result["action_id"] == "approval-action-1"


class TestReminderBackedTypeMapping:
    async def test_create_reminder_event_maps_yearly_rrule_to_yearly_legacy_type(self):
        mod = CalendarModule()
        db = _mock_db()
        mod._db = db
        pool = db.pool

        async def _fetchrow(sql: str, *values):
            columns_sql = sql.split("INSERT INTO reminders (", 1)[1].split(")", 1)[0]
            columns = [part.strip() for part in columns_sql.split(",")]
            row = {"id": uuid.uuid4()}
            row.update(dict(zip(columns, values, strict=False)))
            return row

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)

        with (
            patch.object(mod, "_table_exists", AsyncMock(return_value=True)),
            patch.object(
                mod,
                "_table_columns",
                AsyncMock(
                    return_value=[
                        "label",
                        "message",
                        "type",
                        "reminder_type",
                        "next_trigger_at",
                        "due_at",
                        "timezone",
                        "until_at",
                        "recurrence_rule",
                        "cron",
                        "dismissed",
                        "calendar_event_id",
                        "updated_at",
                    ]
                ),
            ),
        ):
            reminder = await mod._create_reminder_event(
                title="Annual check-in",
                start_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
                timezone="UTC",
                until_at=None,
                recurrence_rule="RRULE:FREQ=YEARLY",
                cron=None,
                action="Check in",
                action_args=None,
                calendar_event_id=uuid.uuid4(),
            )

        assert reminder["type"] == "recurring_yearly"
        assert reminder["reminder_type"] == "recurring"
